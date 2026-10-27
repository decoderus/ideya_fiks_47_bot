"""Вход в веб-интерфейс: кто открыл страницу.

Два способа:
- мини-приложение MAX: страница получает от MAX строку initData с подписью, сделанной токеном бота
  (https://dev.max.ru/docs/webapps/validation) — проверяем подпись и берём номер пользователя;
- одноразовая ссылка из бота (кнопка «🌐 Веб-версия»): пропуск подписан секретом сервера,
  живёт TICKET_TTL и годится один раз — так веб-версию можно открыть и там, где мини-приложение
  не подключено (демо-бот).
После входа страница получает сессию — подписанную строку с номером пользователя и сроком.
"""
import base64
import hashlib
import hmac
import json
import secrets
import time
from urllib.parse import parse_qsl

TICKET_TTL = 10 * 60  # пропуск из бота действует 10 минут
SESSION_TTL = 12 * 60 * 60  # сессия на странице — 12 часов
INIT_DATA_TTL = 24 * 60 * 60  # данные запуска мини-приложения старше суток не принимаем


class AuthError(Exception):
    """Вход не удался: текст можно показать пользователю."""


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _sign(secret: bytes, message: str) -> str:
    return _b64(hmac.new(secret, message.encode(), hashlib.sha256).digest())


def secret_from(bot_token: str, web_secret: str = "") -> bytes:
    """Секрет для пропусков и сессий: задан явно или выводится из токена бота."""
    return hashlib.sha256(("aktiv-web:" + (web_secret or bot_token)).encode()).digest()


def validate_init_data(init_data: str, bot_token: str, now: float | None = None) -> int:
    """Номер MAX пользователя из initData мини-приложения. Подделку или старые данные отклоняет."""
    pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=False)
    keys = [k for k, _ in pairs]
    if keys.count("hash") != 1 or len(keys) != len(set(keys)):
        raise AuthError("Неверные данные запуска")
    data = dict(pairs)
    received = data.pop("hash")
    check_string = "\n".join(f"{k}={data[k]}" for k in sorted(data))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received.lower()):
        raise AuthError("Подпись MAX не сходится — откройте приложение из бота")
    try:
        auth_date = int(data.get("auth_date", "0"))
        user_id = int(json.loads(data["user"])["id"])
    except (KeyError, ValueError, TypeError):
        raise AuthError("Неверные данные запуска") from None
    if (now or time.time()) - auth_date > INIT_DATA_TTL:
        raise AuthError("Данные запуска устарели — откройте приложение заново")
    return user_id


class Tickets:
    """Одноразовые пропуски из бота. Использованные запоминаются до истечения срока."""

    def __init__(self, secret: bytes):
        self.secret = secret
        self._used: dict[str, float] = {}

    def issue(self, max_user_id: int, now: float | None = None) -> str:
        expires = int((now or time.time()) + TICKET_TTL)
        body = f"{max_user_id}.{expires}.{secrets.token_urlsafe(8)}"
        return f"{body}.{_sign(self.secret, 't:' + body)}"

    def consume(self, ticket: str, now: float | None = None) -> int:
        now = now or time.time()
        try:
            user, expires, nonce, signature = ticket.split(".")
            body = f"{user}.{expires}.{nonce}"
            user_id, expires_at = int(user), int(expires)
        except ValueError:
            raise AuthError("Ссылка повреждена — нажмите «🌐 Веб-версия» в боте ещё раз") from None
        if not hmac.compare_digest(signature, _sign(self.secret, "t:" + body)):
            raise AuthError("Ссылка повреждена — нажмите «🌐 Веб-версия» в боте ещё раз")
        if expires_at < now:
            raise AuthError("Ссылка устарела — нажмите «🌐 Веб-версия» в боте ещё раз")
        self._used = {n: t for n, t in self._used.items() if t >= now}
        if nonce in self._used:
            raise AuthError("Ссылка уже использована — нажмите «🌐 Веб-версия» в боте ещё раз")
        self._used[nonce] = expires_at
        return user_id


def issue_session(secret: bytes, max_user_id: int, now: float | None = None) -> str:
    body = f"{max_user_id}.{int((now or time.time()) + SESSION_TTL)}"
    return f"{body}.{_sign(secret, 's:' + body)}"


def check_session(secret: bytes, token: str, now: float | None = None) -> int:
    try:
        user, expires, signature = token.split(".")
        user_id, expires_at = int(user), int(expires)
    except ValueError:
        raise AuthError("Войдите заново") from None
    if not hmac.compare_digest(signature, _sign(secret, f"s:{user}.{expires}")):
        raise AuthError("Войдите заново")
    if expires_at < (now or time.time()):
        raise AuthError("Сессия закончилась — откройте веб-версию из бота заново")
    return user_id
