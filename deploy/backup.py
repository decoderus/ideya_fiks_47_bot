"""Резервная копия базы: безопасно даже при работающем боте.

Запускается таймером aktiv-backup.timer раз в сутки. Копии хранятся KEEP_DAYS дней.
Вручную: sudo -u aktiv /opt/aktiv/app/.venv/bin/python deploy/backup.py
В Docker пути задают переменные BACKUP_DB и BACKUP_DIR, запускает планировщик app/scheduler.py.

Если в .env настроено облако (OFFSITE_TOKEN или OFFSITE_USER) и BACKUP_PASSPHRASE, свежая копия
шифруется и уходит в облако (deploy/offsite.py). Сбой выгрузки не мешает локальной копии: админ получает сообщение в MAX.
"""
import gzip
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # app/ — для настроек

DB = Path(os.environ.get("BACKUP_DB", "/opt/aktiv/app/aktiv.db"))
DIR = Path(os.environ.get("BACKUP_DIR", "/opt/aktiv/backups"))
KEEP_DAYS = 14


def main() -> None:
    if not DB.exists():
        sys.exit(f"База не найдена: {DB}")
    DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    raw = DIR / f"aktiv-{stamp}.db"

    src = sqlite3.connect(DB, timeout=30)
    dst = sqlite3.connect(raw)
    with dst:
        src.backup(dst)
    src.close()
    dst.close()

    with raw.open("rb") as f_in, gzip.open(f"{raw}.gz", "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    raw.unlink()

    border = datetime.now() - timedelta(days=KEEP_DAYS)
    for old in DIR.glob("aktiv-*.db.gz"):
        if datetime.fromtimestamp(old.stat().st_mtime) < border:
            old.unlink()
    print(f"Копия сохранена: {raw}.gz")
    offsite(Path(f"{raw}.gz"))


def offsite(backup: Path) -> None:
    """Зашифрованная копия в облако, если выгрузка настроена."""
    from app.config import get_settings
    from deploy import offsite as offsite_service

    try:
        settings = get_settings()
    except Exception:  # нет BOT_TOKEN и прочих настроек — выгрузка не настроена
        return
    if not offsite_service.configured(settings):
        return
    try:
        name = offsite_service.send(backup, settings)
        print(f"Копия в облаке: {name}")
    except offsite_service.OffsiteError as exc:
        print(f"Копия НЕ отправлена в облако: {exc}")
        offsite_service.alert(
            settings,
            f"⚠️ Ночная копия базы не отправлена в облако: {exc}.\n"
            "Локальная копия на сервере сохранена. Проверьте токен облака и место на диске.",
        )


if __name__ == "__main__":
    main()
