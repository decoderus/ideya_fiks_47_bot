"""Тонкий асинхронный клиент MAX Bot API (https://dev.max.ru/docs-api)."""
import asyncio
import logging
import time
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

# Лимит MAX: не больше 2 сообщений в секунду в один диалог.
MIN_INTERVAL = 0.5

# Паузы между повторами, пока вложение обрабатывается; None — больше не повторять
ATTACHMENT_RETRY_DELAYS = (1, 2, 4, None)


def _find_token(data) -> str | None:
    """Токен в ответе загрузки: {"token": …} или вложенный, например {"photos": {id: {"token": …}}}."""
    if isinstance(data, dict):
        if isinstance(data.get("token"), str):
            return data["token"]
        data = list(data.values())
    if isinstance(data, list):
        for item in data:
            token = _find_token(item)
            if token:
                return token
    return None


def callback_button(text: str, payload: str) -> dict:
    return {"type": "callback", "text": text, "payload": payload}


def link_button(text: str, url: str) -> dict:
    """Кнопка-ссылка: открывает адрес в браузере."""
    return {"type": "link", "text": text, "url": url}


def keyboard(rows: list[list[dict]]) -> list[dict]:
    """Вложение inline_keyboard. rows: список рядов кнопок."""
    return [{"type": "inline_keyboard", "payload": {"buttons": rows}}]


class MaxClient:
    def __init__(self, token: str, base_url: str, ca_bundle: str = ""):
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": token},  # без префикса Bearer
            timeout=httpx.Timeout(100.0, connect=10.0),
            verify=ca_bundle or True,
        )
        self._last_sent: dict[int, float] = {}

    async def close(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, url: str, **kw) -> dict:
        r = await self._client.request(method, url, **kw)
        if r.status_code == 401:
            raise RuntimeError("MAX вернул 401: токен неверен или отозван")
        r.raise_for_status()
        return r.json()

    async def get_me(self) -> dict:
        return await self._request("GET", "/me")

    async def get_updates(self, marker: int | None, timeout: int = 30) -> dict:
        params: dict = {"timeout": timeout, "limit": 100}
        if marker is not None:
            params["marker"] = marker
        return await self._request("GET", "/updates", params=params)

    async def _throttle(self, user_id: int) -> None:
        wait = self._last_sent.get(user_id, 0) + MIN_INTERVAL - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_sent[user_id] = time.monotonic()

    async def send_message(
        self, user_id: int, text: str, attachments: list[dict] | None = None
    ) -> dict:
        body: dict = {"text": text}
        if attachments:
            body["attachments"] = attachments
        # Только что загруженная картинка может ещё обрабатываться:
        # MAX отвечает attachment.not.ready, повторяем с растущей паузой
        for delay in ATTACHMENT_RETRY_DELAYS:
            await self._throttle(user_id)
            try:
                return await self._request(
                    "POST", "/messages", params={"user_id": user_id}, json=body
                )
            except httpx.HTTPStatusError as exc:
                if delay is None or "attachment.not.ready" not in exc.response.text:
                    raise
                log.info("Вложение ещё обрабатывается, повтор через %s с", delay)
                await asyncio.sleep(delay)
        raise AssertionError("unreachable")

    async def send_to_chat(
        self, chat_id: int, text: str, attachments: list[dict] | None = None
    ) -> dict:
        """Сообщение в групповой чат или канал (бот должен быть там администратором)."""
        body: dict = {"text": text}
        if attachments:
            body["attachments"] = attachments
        return await self._request("POST", "/messages", params={"chat_id": chat_id}, json=body)

    async def get_chat(self, chat_id: int) -> dict:
        return await self._request("GET", f"/chats/{chat_id}")

    async def upload_image(self, path: str | Path) -> dict:
        """Загружает картинку в MAX. Возвращает payload для вложения {"token": ...}."""
        path = Path(path)
        upload = await self._request("POST", "/uploads", params={"type": "image"})
        r = await self._client.post(
            upload["url"], files={"data": (path.name, path.read_bytes(), "image/png")}
        )
        r.raise_for_status()
        token = _find_token(r.json())
        if not token:
            raise RuntimeError(f"MAX не вернул токен картинки: {r.text[:200]}")
        return {"token": token}

    async def upload_file(self, filename: str, data: bytes, mime: str) -> dict:
        """Загружает файл (документ) в MAX. Возвращает payload для вложения {"token": ...}."""
        upload = await self._request("POST", "/uploads", params={"type": "file"})
        r = await self._client.post(upload["url"], files={"data": (filename, data, mime)})
        r.raise_for_status()
        try:
            uploaded = r.json()
        except ValueError:
            uploaded = None
        # Токен файла приходит в ответе загрузки, у некоторых версий API — сразу в ответе /uploads
        token = _find_token(uploaded) or _find_token(upload)
        if not token:
            raise RuntimeError(f"MAX не вернул токен файла: {r.text[:200]}")
        return {"token": token}

    async def delete_message(self, mid: str) -> dict:
        return await self._request("DELETE", "/messages", params={"message_id": mid})

    async def answer_callback(
        self,
        callback_id: str,
        text: str | None = None,
        attachments: list[dict] | None = None,
        notification: str | None = None,
    ) -> dict:
        """Ответ на нажатие кнопки: изменить сообщение и/или показать уведомление."""
        body: dict = {}
        if text is not None:
            message: dict = {"text": text}
            if attachments is not None:
                message["attachments"] = attachments
            body["message"] = message
        if notification:
            body["notification"] = notification
        return await self._request(
            "POST", "/answers", params={"callback_id": callback_id}, json=body
        )
