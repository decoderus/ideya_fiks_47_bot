"""Копия базы вне сервера: шифрование и выгрузка в облако по WebDAV (Яндекс Диск и похожие).

Вызывается из deploy/backup.py после локальной копии. Настройки — в .env (app/config.py):
OFFSITE_USER, OFFSITE_PASSWORD (пароль приложения), BACKUP_PASSPHRASE; без них ничего не делает.

Файл шифруется AES-256 (openssl, ключ из пароля через PBKDF2), в облаке лежит только *.db.gz.enc.
Восстановить:
    openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -in aktiv-….db.gz.enc -out aktiv.db.gz
    gunzip aktiv.db.gz
(openssl спросит пароль — значение BACKUP_PASSPHRASE).
В облаке копии хранятся OFFSITE_KEEP_DAYS дней, старые удаляются.
"""
import os
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote, unquote
from xml.etree import ElementTree

import httpx

OPENSSL = ("openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-salt")
STAMP = re.compile(r"aktiv-(\d{4}-\d{2}-\d{2}_\d{2}-\d{2})\.db\.gz\.enc$")
TIMEOUT = httpx.Timeout(120.0, connect=15.0)


class OffsiteError(RuntimeError):
    """Копию не удалось зашифровать или выгрузить."""


def encrypt(src: Path, dst: Path, passphrase: str) -> None:
    run = subprocess.run(
        [*OPENSSL, "-in", str(src), "-out", str(dst), "-pass", "env:AKTIV_BACKUP_PASS"],
        env={**os.environ, "AKTIV_BACKUP_PASS": passphrase},
        capture_output=True, text=True,
    )
    if run.returncode != 0:
        raise OffsiteError(f"openssl: {run.stderr.strip()[:200]}")


def decrypt(src: Path, dst: Path, passphrase: str) -> None:
    run = subprocess.run(
        [*OPENSSL, "-d", "-in", str(src), "-out", str(dst), "-pass", "env:AKTIV_BACKUP_PASS"],
        env={**os.environ, "AKTIV_BACKUP_PASS": passphrase},
        capture_output=True, text=True,
    )
    if run.returncode != 0:
        raise OffsiteError(f"openssl: {run.stderr.strip()[:200]}")


class WebDav:
    def __init__(self, base_url: str, user: str, password: str, folder: str, transport=None):
        self.folder = folder.strip("/")
        self.client = httpx.Client(
            base_url=base_url.rstrip("/"), auth=(user, password), timeout=TIMEOUT, transport=transport
        )

    def _path(self, name: str = "") -> str:
        return f"/{quote(self.folder)}/{quote(name)}"

    def _check(self, response: httpx.Response, action: str) -> None:
        if response.status_code == 401:
            raise OffsiteError("облако не приняло логин или пароль приложения")
        if response.status_code >= 400:
            raise OffsiteError(f"{action}: HTTP {response.status_code}")

    def ensure_folder(self) -> None:
        response = self.client.request("MKCOL", self._path())
        if response.status_code not in (201, 405):  # 405 — папка уже есть
            self._check(response, "создание папки")

    def upload(self, path: Path) -> None:
        with path.open("rb") as f:
            response = self.client.put(self._path(path.name), content=f.read())
        self._check(response, "загрузка")

    def list_names(self) -> list[str]:
        response = self.client.request("PROPFIND", self._path(), headers={"Depth": "1"})
        self._check(response, "список файлов")
        names = []
        for href in ElementTree.fromstring(response.content).iter("{DAV:}href"):
            name = unquote((href.text or "").rstrip("/").rsplit("/", 1)[-1])
            if STAMP.match(name):
                names.append(name)
        return sorted(names)

    def delete(self, name: str) -> None:
        self._check(self.client.delete(self._path(name)), "удаление")

    def close(self) -> None:
        self.client.close()


def expired(names: list[str], keep_days: int, now: datetime) -> list[str]:
    """Копии старше keep_days дней (по дате в имени файла)."""
    border = now - timedelta(days=keep_days)
    return [n for n in names if datetime.strptime(STAMP.match(n).group(1), "%Y-%m-%d_%H-%M") < border]


def send(backup: Path, settings, transport=None, now: datetime | None = None) -> str:
    """Зашифровать копию, выгрузить в облако и удалить там старые. Возвращает имя файла в облаке."""
    if not settings.backup_passphrase:
        raise OffsiteError("не задан BACKUP_PASSPHRASE — без шифрования копию в облако не отправляем")
    encrypted = backup.with_name(backup.name + ".enc")
    encrypt(backup, encrypted, settings.backup_passphrase)
    dav = WebDav(settings.offsite_url, settings.offsite_user, settings.offsite_password,
                 settings.offsite_dir, transport)
    try:
        dav.ensure_folder()
        dav.upload(encrypted)
        for name in expired(dav.list_names(), settings.offsite_keep_days, now or datetime.now()):
            dav.delete(name)
    except httpx.HTTPError as exc:
        raise OffsiteError(f"нет связи с облаком: {type(exc).__name__}") from exc
    finally:
        dav.close()
        encrypted.unlink(missing_ok=True)
    return encrypted.name


def alert(settings, text: str) -> None:
    """Сообщить админам в MAX, что копия не ушла в облако. Само никогда не падает."""
    ids = settings.alert_id_set or settings.admin_id_set
    try:
        with httpx.Client(base_url=settings.max_api_base, headers={"Authorization": settings.bot_token},
                          timeout=15.0, verify=settings.ca_bundle or True) as client:
            for user_id in ids:
                client.post("/messages", params={"user_id": user_id}, json={"text": text})
    except Exception as exc:  # noqa: BLE001 — оповещение не должно ронять бэкап
        print(f"Не удалось отправить оповещение: {exc}")
