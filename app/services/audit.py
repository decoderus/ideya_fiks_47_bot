"""Журнал действий модераторов и админов — файл на сервере, без интерфейса в боте.

Строка: дата и время (МСК) | кто | роль | id в боте и в MAX | действие | подробности.
Пример:
2026-10-19 08:15:02 МСК | Петров Пётр | модератор | id 41, max 123456 | teacher.approve | Смирнова Анна (заявка №7)
"""
import logging
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.db.models import Role, User

MSK = timezone(timedelta(hours=3))
ROLE_NAMES = {
    Role.admin: "админ",
    Role.moderator: "модератор",
    Role.teacher: "учитель",
    Role.student: "ученик",
}

log = logging.getLogger("aktiv.audit")
log.propagate = False  # только в файл, не в общий лог
log.setLevel(logging.INFO)


def setup(path: str) -> None:
    """Писать журнал в файл: до 5 МБ, плюс 10 предыдущих файлов."""
    if not path or any(isinstance(h, RotatingFileHandler) for h in log.handlers):
        return
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(path, maxBytes=5_000_000, backupCount=10, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(handler)


def record(actor: User, action: str, details: str = "") -> None:
    stamp = datetime.now(MSK).strftime("%Y-%m-%d %H:%M:%S МСК")
    who = actor.full_name or actor.name or "—"
    role = ROLE_NAMES.get(actor.role, str(actor.role))
    log.info(
        "%s | %s | %s | id %s, max %s | %s | %s",
        stamp, who, role, actor.id, actor.max_user_id, action, details.replace("\n", " "),
    )
