"""Картинки квестов и наград при переезде на новый токен бота.

Вложения MAX хранятся в базе как payload ({"token": ...} и иногда "url").
Токен вложения может не работать у другого бота. Скрипт:
  проверка  — новый бот присылает пользователю TO каждую картинку активных квестов и наград;
  --fix     — неоткрывшиеся картинки скачивает и загружает заново через новый бот.
              Если в payload нет url, его получает старый бот (OLD_BOT_TOKEN): присылает
              картинку пользователю TO, берёт url из ответа и сразу удаляет сообщение.

TO должен был нажать «Старт» в обоих ботах. Перед --fix сделайте копию базы.

    BOT_TOKEN=новый OLD_BOT_TOKEN=старый python deploy/move_media.py --db aktiv.db --to 123 [--fix]
"""
import argparse
import asyncio
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.max_api import MaxClient  # noqa: E402

API = os.environ.get("MAX_API_BASE", "https://platform-api2.max.ru")
CA = os.environ.get("CA_BUNDLE", "")
TABLES = (("quests", "квест"), ("rewards", "награда"))


def items(db: sqlite3.Connection) -> list[tuple[str, str, int, str, dict]]:
    rows = []
    for table, label in TABLES:
        for row_id, title, photo in db.execute(
            f"SELECT id, title, photo FROM {table} WHERE is_active AND photo IS NOT NULL"
        ):
            # JSON-колонка без картинки может хранить строку «null»
            if json.loads(photo):
                rows.append((table, label, row_id, title, json.loads(photo)))
    return rows


def image(photo: dict) -> list[dict]:
    payload = {"token": photo["token"]} if photo.get("token") else {"url": photo.get("url")}
    return [{"type": "image", "payload": payload}]


async def sends(client: MaxClient, to: int, text: str, photo: dict) -> dict | None:
    """Ответ MAX или None, если вложение не принято."""
    try:
        return await client.send_message(to, text, image(photo))
    except httpx.HTTPStatusError as exc:
        print(f"    MAX: {exc.response.status_code} {exc.response.text[:150]}")
        return None


def sent_url(resp: dict) -> str | None:
    for a in resp.get("message", {}).get("body", {}).get("attachments", []):
        if a.get("type") == "image" and a.get("payload", {}).get("url"):
            return a["payload"]["url"]
    return None


async def reupload(new: MaxClient, old: MaxClient | None, to: int, photo: dict) -> dict | None:
    url = photo.get("url")
    if not url and old is not None:
        resp = await sends(old, to, "Перенос картинки (сообщение удалится)", photo)
        if resp:
            url = sent_url(resp)
            mid = resp.get("message", {}).get("body", {}).get("mid")
            if mid:
                await old.delete_message(mid)
    if not url:
        return None
    async with httpx.AsyncClient(verify=CA or True, timeout=60) as http:
        r = await http.get(url)
        r.raise_for_status()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "image.png"
        path.write_bytes(r.content)
        return await new.upload_image(path)


async def run(db_path: str, to: int, fix: bool, new: MaxClient, old: MaxClient | None) -> int:
    db = sqlite3.connect(db_path)
    broken = 0
    for table, label, row_id, title, photo in items(db):
        text = f"Проверка: {label} #{row_id} «{title}»"
        if await sends(new, to, text, photo):
            print(f"OK    {label} #{row_id} {title}")
            continue
        if not fix:
            print(f"НЕТ   {label} #{row_id} {title}")
            broken += 1
            continue
        fresh = await reupload(new, old, to, photo)
        if fresh and await sends(new, to, text + " (перезалита)", fresh):
            with db:
                db.execute(f"UPDATE {table} SET photo = ? WHERE id = ?", (json.dumps(fresh), row_id))
            print(f"ЗАМЕНА {label} #{row_id} {title}")
        else:
            print(f"НЕТ   {label} #{row_id} {title}: перезалить не удалось")
            broken += 1
    db.close()
    print(f"Не открываются: {broken}")
    return broken


async def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--db", default="aktiv.db")
    p.add_argument("--to", type=int, required=True, help="MAX user_id получателя проверки")
    p.add_argument("--fix", action="store_true")
    args = p.parse_args()
    new = MaxClient(os.environ["BOT_TOKEN"], API, CA)
    old_token = os.environ.get("OLD_BOT_TOKEN")
    old = MaxClient(old_token, API, CA) if old_token else None
    try:
        broken = await run(args.db, args.to, args.fix, new, old)
    finally:
        await new.close()
        if old:
            await old.close()
    sys.exit(1 if broken else 0)


if __name__ == "__main__":
    asyncio.run(main())
