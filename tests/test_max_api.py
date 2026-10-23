"""Клиент MAX: загрузка картинки и повтор отправки, пока вложение обрабатывается."""
import json

import httpx
import pytest

from app import max_api
from app.max_api import MaxClient


def client_with(handler) -> MaxClient:
    client = MaxClient("TOKEN", "https://api.test")
    client._client = httpx.AsyncClient(
        base_url="https://api.test",
        headers={"Authorization": "TOKEN"},
        transport=httpx.MockTransport(handler),
    )
    return client


async def test_upload_image(tmp_path):
    image = tmp_path / "pic.png"
    image.write_bytes(b"png-bytes")
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, str(request.url)))
        if request.url.path == "/uploads":
            assert request.url.params["type"] == "image"
            return httpx.Response(200, json={"url": "https://upload.test/u/1"})
        assert b'name="data"' in request.content and b"png-bytes" in request.content
        return httpx.Response(200, json={"photos": {"abc": {"token": "img-token"}}})

    client = client_with(handler)
    assert await client.upload_image(image) == {"token": "img-token"}
    assert seen == [
        ("POST", "https://api.test/uploads?type=image"),
        ("POST", "https://upload.test/u/1"),
    ]
    await client.close()


async def test_send_retries_while_attachment_not_ready(monkeypatch):
    monkeypatch.setattr(max_api, "ATTACHMENT_RETRY_DELAYS", (0, 0, None))
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        if len(calls) < 3:
            return httpx.Response(400, json={"code": "attachment.not.ready", "message": "…"})
        return httpx.Response(200, json={"message": {"body": {"mid": "m1"}}})

    client = client_with(handler)
    monkeypatch.setattr(client, "_throttle", lambda user_id: _noop())
    result = await client.send_message(5, "текст", [{"type": "image", "payload": {"token": "t"}}])
    assert result["message"]["body"]["mid"] == "m1"
    assert len(calls) == 3
    await client.close()


async def test_send_gives_up_and_other_errors_not_retried(monkeypatch):
    monkeypatch.setattr(max_api, "ATTACHMENT_RETRY_DELAYS", (0, None))
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        code = "attachment.not.ready" if request.url.params["user_id"] == "5" else "chat.denied"
        return httpx.Response(400, json={"code": code})

    client = client_with(handler)
    monkeypatch.setattr(client, "_throttle", lambda user_id: _noop())
    with pytest.raises(httpx.HTTPStatusError):
        await client.send_message(5, "текст")
    assert len(calls) == 2  # одна попытка + один повтор
    with pytest.raises(httpx.HTTPStatusError):
        await client.send_message(6, "текст")
    assert len(calls) == 3  # другие ошибки не повторяем
    await client.close()


async def _noop():
    return None
