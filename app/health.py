"""Здоровье бота: признак жизни для HEALTHCHECK и тексты оповещений администраторам.

Признак жизни — файл с временем последнего успешного обращения к MAX и отметкой
«остановлен штатно». По нему:
- HEALTHCHECK Docker (python -m app.health) считает бота живым, пока запись свежая;
- при запуске видно, что прошлый запуск закончился сбоем (штатной отметки нет).

Проверка без Docker: python -m app.health — код выхода 0, если бот жив.
"""
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.services.contests import MSK

# Опрос MAX длится до 30 с; свежее этого — бот жив
MAX_AGE = timedelta(minutes=2)
# Не перезаписывать файл чаще (каждый опрос — лишняя запись на диск)
BEAT_EVERY = timedelta(seconds=15)
# MAX не отвечал дольше — сообщить админам, когда связь вернётся
OUTAGE_ALERT = timedelta(minutes=3)
# Ошибки обработки событий — не чаще одного сообщения в час
ERROR_ALERT_EVERY = timedelta(hours=1)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _msk(moment: datetime) -> str:
    return moment.astimezone(MSK).strftime("%d.%m %H:%M")


def _duration(delta: timedelta) -> str:
    minutes = max(round(delta.total_seconds() / 60), 1)
    if minutes < 60:
        return f"{minutes} мин"
    return f"{minutes // 60} ч {minutes % 60} мин"


class Heartbeat:
    """Файл признака жизни: {"at": время UTC, "stopped": штатная остановка}."""

    def __init__(self, path: str):
        self.path = Path(path)
        self._last_write: datetime | None = None

    def read(self) -> dict | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return {"at": datetime.fromisoformat(data["at"]), "stopped": bool(data.get("stopped"))}
        except (OSError, ValueError, KeyError):
            return None

    def _write(self, at: datetime, stopped: bool) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"at": at.isoformat(), "stopped": stopped}), encoding="utf-8")
        tmp.replace(self.path)  # целиком: проверка не прочитает половину файла

    def beat(self, now: datetime | None = None) -> None:
        now = now or _now()
        if self._last_write and now - self._last_write < BEAT_EVERY:
            return
        self._write(now, stopped=False)
        self._last_write = now

    def stopped(self, now: datetime | None = None) -> None:
        self._write(now or _now(), stopped=True)


def is_alive(record: dict | None, now: datetime | None = None) -> bool:
    return record is not None and (now or _now()) - record["at"] <= MAX_AGE


def restart_notice(previous: dict | None, now: datetime | None = None) -> str | None:
    """Сообщение, если прошлый запуск закончился сбоем, а не штатной остановкой."""
    if previous is None or previous["stopped"]:
        return None
    now = now or _now()
    return (
        "⚠️ Бот перезапустился после сбоя.\n"
        f"Последний раз был на связи: {_msk(previous['at'])} МСК, "
        f"перерыв — около {_duration(now - previous['at'])}.\n"
        "Сейчас бот снова работает."
    )


@dataclass
class Watchdog:
    """Следит за связью с MAX и ошибками обработки; решает, когда писать админам."""

    outage_since: datetime | None = None
    last_error_alert: datetime | None = None
    errors_since_alert: int = 0
    last_error: str = ""

    def poll_failed(self, now: datetime | None = None) -> None:
        if self.outage_since is None:
            self.outage_since = now or _now()

    def poll_ok(self, now: datetime | None = None) -> str | None:
        """Связь есть. Если до этого её долго не было — текст для админов."""
        since, self.outage_since = self.outage_since, None
        now = now or _now()
        if since is None or now - since < OUTAGE_ALERT:
            return None
        return (
            f"⚠️ MAX не отвечал боту {_duration(now - since)}: "
            f"с {_msk(since)} до {_msk(now)} МСК.\n"
            "Связь восстановлена. Сообщения, отправленные в это время, бот мог не получить."
        )

    def error(self, description: str, now: datetime | None = None) -> str | None:
        """Ошибка при обработке события. Текст для админов — не чаще раза в час."""
        now = now or _now()
        self.errors_since_alert += 1
        self.last_error = description
        if self.last_error_alert and now - self.last_error_alert < ERROR_ALERT_EVERY:
            return None
        count, self.errors_since_alert = self.errors_since_alert, 0
        self.last_error_alert = now
        more = f" (всего ошибок с прошлого сообщения: {count})" if count > 1 else ""
        return (
            f"⚠️ Ошибка при обработке действия пользователя{more}.\n"
            f"{description}\n"
            "Бот продолжает работать. Подробности — в журнале на сервере. "
            "Следующее такое сообщение — не раньше чем через час."
        )


def main() -> None:
    """HEALTHCHECK: 0 — бот жив, 1 — давно не был на связи с MAX или не запущен."""
    from app.config import get_settings

    record = Heartbeat(get_settings().heartbeat_file).read()
    if is_alive(record):
        print(f"ok: на связи {_msk(record['at'])} МСК")
        sys.exit(0)
    when = _msk(record["at"]) if record else "никогда"
    print(f"unhealthy: последний раз на связи — {when}")
    sys.exit(1)


if __name__ == "__main__":
    main()
