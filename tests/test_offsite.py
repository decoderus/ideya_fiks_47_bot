"""Копии базы вне сервера: шифрование, выгрузка по WebDAV, удаление старых копий в облаке."""
import gzip
import shutil
from datetime import datetime
from types import SimpleNamespace

import httpx
import pytest

from deploy import offsite

pytestmark = pytest.mark.skipif(shutil.which("openssl") is None, reason="нужен openssl")

NOW = datetime(2026, 10, 26, 3, 30)
LISTING = """<?xml version="1.0" encoding="utf-8"?>
<d:multistatus xmlns:d="DAV:">
  <d:response><d:href>/aktiv-backups/</d:href></d:response>
  <d:response><d:href>/aktiv-backups/aktiv-2026-09-20_03-30.db.gz.enc</d:href></d:response>
  <d:response><d:href>/aktiv-backups/aktiv-2026-10-20_03-30.db.gz.enc</d:href></d:response>
  <d:response><d:href>/aktiv-backups/%D0%B7%D0%B0%D0%BC%D0%B5%D1%82%D0%BA%D0%B8.txt</d:href></d:response>
</d:multistatus>"""


def settings(**kw):
    base = dict(
        offsite_token="", offsite_url="https://webdav.example.ru", offsite_user="school", offsite_password="app-pass",
        offsite_dir="aktiv-backups", offsite_keep_days=30, backup_passphrase="секрет 123",
    )
    return SimpleNamespace(**{**base, **kw})


def backup_file(tmp_path):
    path = tmp_path / "aktiv-2026-10-26_03-30.db.gz"
    path.write_bytes(gzip.compress("ФИО учеников".encode() * 100))
    return path


def test_encrypt_roundtrip(tmp_path):
    src = backup_file(tmp_path)
    enc, back = tmp_path / "x.enc", tmp_path / "x.gz"
    offsite.encrypt(src, enc, "секрет 123")
    assert "ФИО".encode() not in enc.read_bytes() and enc.read_bytes() != src.read_bytes()
    offsite.decrypt(enc, back, "секрет 123")
    assert back.read_bytes() == src.read_bytes()
    with pytest.raises(offsite.OffsiteError):
        offsite.decrypt(enc, tmp_path / "bad", "не тот пароль")


def test_send_uploads_and_cleans(tmp_path):
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        assert request.headers["authorization"].startswith("Basic ")
        if request.method == "MKCOL":
            return httpx.Response(405)  # папка уже есть
        if request.method == "PROPFIND":
            return httpx.Response(207, content=LISTING.encode())
        return httpx.Response(201 if request.method == "PUT" else 204)

    src = backup_file(tmp_path)
    name = offsite.send(src, settings(), httpx.MockTransport(handler), now=NOW)
    assert name == "aktiv-2026-10-26_03-30.db.gz.enc"
    assert ("PUT", "/aktiv-backups/aktiv-2026-10-26_03-30.db.gz.enc") in requests
    # старше 30 дней — удалена, свежая и чужой файл остаются
    assert [r for r in requests if r[0] == "DELETE"] == [("DELETE", "/aktiv-backups/aktiv-2026-09-20_03-30.db.gz.enc")]
    assert not (tmp_path / f"{src.name}.enc").exists()  # зашифрованный файл не копится на сервере
    assert src.exists()


def test_errors(tmp_path):
    src = backup_file(tmp_path)
    with pytest.raises(offsite.OffsiteError, match="BACKUP_PASSPHRASE"):
        offsite.send(src, settings(backup_passphrase=""))
    deny = httpx.MockTransport(lambda request: httpx.Response(401))
    with pytest.raises(offsite.OffsiteError, match="пароль приложения"):
        offsite.send(src, settings(), deny)

    def offline(request):
        raise httpx.ConnectError("нет сети")

    with pytest.raises(offsite.OffsiteError, match="нет связи"):
        offsite.send(src, settings(), httpx.MockTransport(offline))
    assert not (tmp_path / f"{src.name}.enc").exists()


def test_yandex_disk_rest(tmp_path):
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.host, request.url.path, dict(request.url.params)))
        if request.url.host == "uploader.example":
            assert "authorization" not in request.headers  # токен не уходит на сервер загрузки
            return httpx.Response(201)
        assert request.headers["authorization"] == "OAuth tok"
        if request.url.path.endswith("/resources/upload"):
            return httpx.Response(200, json={"href": "https://uploader.example/put/1", "method": "PUT"})
        if request.method == "GET":
            return httpx.Response(200, json={"_embedded": {"items": [
                {"name": "aktiv-2026-09-01_03-30.db.gz.enc"}, {"name": "aktiv-2026-10-25_03-30.db.gz.enc"},
                {"name": "readme.txt"},
            ]}})
        return httpx.Response(204)

    src = backup_file(tmp_path)
    offsite.send(src, settings(offsite_token="tok", offsite_user=""), httpx.MockTransport(handler), now=NOW)
    upload = next(r for r in requests if r[2].endswith("/resources/upload"))
    assert upload[3]["path"] == "app:/aktiv-2026-10-26_03-30.db.gz.enc"
    assert any(r[1] == "uploader.example" and r[0] == "PUT" for r in requests)
    deletes = [r[3]["path"] for r in requests if r[0] == "DELETE"]
    assert deletes == ["app:/aktiv-2026-09-01_03-30.db.gz.enc"]

    expired_token = httpx.MockTransport(lambda request: httpx.Response(401))
    with pytest.raises(offsite.OffsiteError, match="токен"):
        offsite.send(src, settings(offsite_token="old"), expired_token)
