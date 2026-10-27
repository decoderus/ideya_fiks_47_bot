"""Веб-интерфейс: подпись мини-приложения MAX, одноразовые пропуски, сессии, права доступа к API."""
import hashlib
import hmac
import json
import time
from urllib.parse import parse_qs, urlencode, urlparse

import pytest
from aiohttp.test_utils import TestClient, TestServer

from app.db.models import SchoolClass
from app.web import auth
from app.web import server as web_server
from tests.test_bot import (  # noqa: F401  env — фикстура
    buttons,
    cb,
    class_id,
    env,
    make_teacher,
    register,
)

TOKEN = "bot-token-123"
SECRET = auth.secret_from(TOKEN)


def init_data(user_id: int, token: str = TOKEN, auth_date: int | None = None, **extra) -> str:
    """initData так, как её подписывает MAX (https://dev.max.ru/docs/webapps/validation)."""
    params = {
        "auth_date": str(auth_date or int(time.time())),
        "query_id": "q-1",
        "user": json.dumps({"id": user_id, "first_name": "Иван"}, ensure_ascii=False),
        **extra,
    }
    check = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    params["hash"] = hmac.new(key, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(params)


def test_init_data_signature():
    assert auth.validate_init_data(init_data(5), TOKEN) == 5
    with pytest.raises(auth.AuthError, match="Подпись"):
        auth.validate_init_data(init_data(5, token="чужой токен"), TOKEN)
    forged = init_data(5).replace("%22id%22%3A+5", "%22id%22%3A+6")
    with pytest.raises(auth.AuthError):
        auth.validate_init_data(forged, TOKEN)
    with pytest.raises(auth.AuthError, match="устарели"):
        auth.validate_init_data(init_data(5, auth_date=int(time.time()) - 2 * 86400), TOKEN)
    with pytest.raises(auth.AuthError):
        auth.validate_init_data(init_data(5) + "&hash=abc", TOKEN)  # два hash


def test_tickets_and_sessions():
    tickets = auth.Tickets(SECRET)
    ticket = tickets.issue(5)
    assert tickets.consume(ticket) == 5
    with pytest.raises(auth.AuthError, match="использована"):
        tickets.consume(ticket)
    with pytest.raises(auth.AuthError, match="устарела"):
        tickets.consume(tickets.issue(5, now=time.time() - 3600))
    with pytest.raises(auth.AuthError, match="повреждена"):
        tickets.consume(tickets.issue(5).replace("5.", "6.", 1))
    with pytest.raises(auth.AuthError, match="повреждена"):
        auth.Tickets(auth.secret_from("другой")).consume(tickets.issue(5))

    session = auth.issue_session(SECRET, 7)
    assert auth.check_session(SECRET, session) == 7
    with pytest.raises(auth.AuthError):
        auth.check_session(SECRET, session.replace("7.", "8.", 1))
    with pytest.raises(auth.AuthError, match="закончилась"):
        auth.check_session(SECRET, auth.issue_session(SECRET, 7, now=time.time() - 86400))


async def web_client(bot, sessions):
    bot.web_tickets = auth.Tickets(SECRET)
    bot.web_url = "https://aktiv.example"
    client = TestClient(TestServer(web_server.make_app(sessions, TOKEN, SECRET, bot.web_tickets)))
    await client.start_server()
    return client


async def login_from_bot(bot, fake, web, uid) -> str:
    """Кнопка «🌐 Веб-версия» в боте → ссылка с пропуском → сессия."""
    await bot.handle_update(cb(uid, "web:open"))
    link = next(b for b in buttons(fake.answers[-1]["attachments"]) if b["type"] == "link")["url"]
    assert link.startswith("https://aktiv.example/?t=")
    ticket = parse_qs(urlparse(link).query)["t"][0]
    response = await web.post("/api/login", json={"ticket": ticket})
    assert response.status == 200
    return (await response.json())["token"]


async def test_student_sees_own_data(env):
    bot, fake, sessions = env
    await make_teacher(bot, 7)
    await register(bot, 5, "Иванов Иван")
    async with sessions() as s:
        s.add(SchoolClass(name="Другой класс"))
        await s.commit()
    web = await web_client(bot, sessions)
    try:
        assert (await web.get("/")).status == 200
        assert (await web.get("/api/me")).status == 401  # без входа ничего не отдаём

        await bot.handle_update(cb(5, "menu:main"))
        assert "web:open" in [b["payload"] for b in buttons(fake.answers[-1]["attachments"])]
        token = await login_from_bot(bot, fake, web, 5)
        headers = {"Authorization": f"Bearer {token}"}

        me = await (await web.get("/api/me", headers=headers)).json()
        assert me["name"] == "Иванов Иван" and me["role"] == "student" and not me["staff"]
        assert "profile" in me and me["successes"] == []

        rating = await (await web.get("/api/rating", headers=headers)).json()
        assert rating["mine"]["place"] == 1 and [c["name"] for c in rating["classes"]] == ["Тестовый класс"]
        own = await class_id(sessions)
        assert (await web.get(f"/api/rating?class_id={own}", headers=headers)).status == 200
        other = await class_id(sessions, "Другой класс")
        assert (await web.get(f"/api/rating?class_id={other}", headers=headers)).status == 403
        assert (await web.get(f"/api/pulse/{own}", headers=headers)).status == 403
        assert (await web.get("/api/classes", headers=headers)).status == 403
    finally:
        await web.close()


async def test_teacher_sees_pulse_and_mini_app_login(env):
    bot, fake, sessions = env
    await make_teacher(bot, 7)
    await register(bot, 5, "Иванов Иван")
    web = await web_client(bot, sessions)
    try:
        # вход как мини-приложение: подпись MAX вместо пропуска
        response = await web.post("/api/login", json={"initData": init_data(7)})
        token = (await response.json())["token"]
        headers = {"Authorization": f"Bearer {token}"}
        me = await (await web.get("/api/me", headers=headers)).json()
        assert me["staff"] and me["classes"] == ["Тестовый класс"]
        classes = await (await web.get("/api/classes", headers=headers)).json()
        assert classes[0]["own"]
        pulse = await (await web.get(f"/api/pulse/{classes[0]['id']}", headers=headers)).json()
        assert pulse["students"] == 1 and pulse["roster"][0]["name"] == "Иванов Иван"
        assert (await web.get("/api/pulse/999", headers=headers)).status == 404

        # поддельная подпись и незарегистрированный пользователь
        bad = await web.post("/api/login", json={"initData": init_data(7, token="чужой")})
        assert bad.status == 401
        stranger = await web.post("/api/login", json={"initData": init_data(424242)})
        assert stranger.status == 403
    finally:
        await web.close()


async def test_no_web_button_without_config(env):
    bot, fake, _ = env
    await register(bot, 5)
    await bot.handle_update(cb(5, "menu:main"))
    assert "web:open" not in [b["payload"] for b in buttons(fake.answers[-1]["attachments"])]
    await bot.handle_update(cb(5, "web:open"))
    assert "не подключена" in fake.answers[-1]["notification"]
