"""Общие помощники экранов бота."""
from datetime import datetime, timedelta, timezone

# Экран: текст + вложения (клавиатура, фото). На текст приходит новым сообщением,
# на нажатие кнопки им заменяется текущее сообщение.
Screen = tuple[str, list[dict] | None]

MSK = timezone(timedelta(hours=3))

# Пауза между сообщениями массовой рассылки: общий лимит MAX — 30 запросов в секунду
BROADCAST_INTERVAL = 0.05


def parse_id(payload: str) -> int | None:
    """Число в конце payload: «q:open:5» -> 5."""
    try:
        return int(payload.rsplit(":", 1)[1])
    except (IndexError, ValueError):
        return None


def parse_ids(payload: str, count: int) -> list[int] | None:
    """Последние count чисел payload: «enr:del:7:3», 2 -> [7, 3]."""
    try:
        return [int(x) for x in payload.split(":")[-count:]]
    except ValueError:
        return None


def find_attachment(attachments: list[dict], kind: str) -> dict | None:
    """Payload первого вложения типа kind («image», «video»)."""
    for a in attachments:
        if a.get("type") == kind and a.get("payload"):
            return a["payload"]
    return None


def find_image(attachments: list[dict]) -> dict | None:
    return find_attachment(attachments, "image")


def image_attachment(photo: dict) -> dict:
    payload = {"token": photo["token"]} if photo.get("token") else {"url": photo.get("url")}
    return {"type": "image", "payload": payload}


def video_attachment(video: dict) -> dict:
    return {"type": "video", "payload": {"token": video.get("token")}}


def audio_attachment(audio: dict) -> dict:
    payload = {"token": audio["token"]} if audio.get("token") else {"url": audio.get("url")}
    return {"type": "audio", "payload": payload}


def broadcast_media(photo: dict | None, audio: dict | None) -> list[dict]:
    """Вложения оповещения: фото и голосовое (или аудио)."""
    media = [image_attachment(photo)] if photo else []
    if audio:
        media.append(audio_attachment(audio))
    return media


def format_date(moment: datetime) -> str:
    # SQLite отдаёт время без часового пояса; храним в UTC, показываем по Москве
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(MSK).strftime("%d.%m.%Y %H:%M")


def format_day(moment: datetime) -> str:
    return format_date(moment)[:5]  # «18.10»


def days_word(n: int) -> str:
    """1 день, 2 дня, 5 дней."""
    if n % 10 == 1 and n % 100 != 11:
        return "день"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "дня"
    return "дней"
