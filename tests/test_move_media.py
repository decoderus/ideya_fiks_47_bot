"""Перенос картинок квестов и наград на новый токен бота (deploy/move_media.py)."""
import json
import sqlite3

import httpx

from deploy import move_media

URL = "https://i.example/old.png"


def http_error() -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "https://max/messages")
    return httpx.HTTPStatusError(
        "bad", request=req, response=httpx.Response(400, request=req, text="attachment.invalid")
    )


class NewBot:
    """Новый бот: старые токены вложений не принимает."""

    def __init__(self):
        self.sent = []
        self.uploaded = []

    async def send_message(self, to, text, attachments):
        token = attachments[0]["payload"].get("token")
        if token and token.startswith("old"):
            raise http_error()
        self.sent.append((to, text, attachments))
        return {"message": {"body": {"mid": "m-new"}}}

    async def upload_image(self, path):
        self.uploaded.append(path.read_bytes())
        return {"token": "new-1"}


class OldBot:
    def __init__(self):
        self.deleted = []

    async def send_message(self, to, text, attachments):
        payload = {"photo_id": 1, "token": attachments[0]["payload"]["token"], "url": URL}
        return {"message": {"body": {"mid": "m-old", "attachments": [{"type": "image", "payload": payload}]}}}

    async def delete_message(self, mid):
        self.deleted.append(mid)


def make_db(path) -> str:
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE quests (id INTEGER, title TEXT, photo TEXT, is_active BOOLEAN)")
    db.execute("CREATE TABLE rewards (id INTEGER, title TEXT, photo TEXT, is_active BOOLEAN)")
    db.execute("INSERT INTO quests VALUES (1, 'Зарядка', ?, 1)", (json.dumps({"token": "old-q"}),))
    db.execute("INSERT INTO quests VALUES (2, 'Закрытый', ?, 0)", (json.dumps({"token": "old-x"}),))
    db.execute("INSERT INTO rewards VALUES (1, 'Обед', ?, 1)", (json.dumps({"token": "fine"}),))
    db.execute("INSERT INTO rewards VALUES (2, 'Без картинки', 'null', 1)")
    db.commit()
    db.close()
    return str(path)


def mock_download(monkeypatch):
    real = httpx.AsyncClient
    transport = httpx.MockTransport(lambda req: httpx.Response(200, content=b"PNG"))
    monkeypatch.setattr(
        move_media.httpx, "AsyncClient", lambda **kw: real(transport=transport)
    )


async def test_check_only(tmp_path):
    db = make_db(tmp_path / "a.db")
    broken = await move_media.run(db, 7, fix=False, new=NewBot(), old=None)
    assert broken == 1  # квест 1; закрытый квест 2 не проверяется


async def test_fix_reuploads_through_old_bot(tmp_path, monkeypatch):
    mock_download(monkeypatch)
    db = make_db(tmp_path / "a.db")
    new, old = NewBot(), OldBot()
    broken = await move_media.run(db, 7, fix=True, new=new, old=old)
    assert broken == 0
    assert new.uploaded == [b"PNG"]
    assert old.deleted == ["m-old"]
    con = sqlite3.connect(db)
    photos = dict(con.execute("SELECT id, photo FROM quests"))
    assert json.loads(photos[1]) == {"token": "new-1"}
    assert json.loads(photos[2]) == {"token": "old-x"}  # закрытые не трогаем
    assert con.execute("SELECT photo FROM rewards").fetchone()[0] == json.dumps({"token": "fine"})


async def test_fix_without_old_bot_and_url(tmp_path):
    db = make_db(tmp_path / "a.db")
    broken = await move_media.run(db, 7, fix=True, new=NewBot(), old=None)
    assert broken == 1
