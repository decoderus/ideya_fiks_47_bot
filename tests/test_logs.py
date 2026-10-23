"""Журналы в «Статистике»: движение монет и действия персонала (только админ)."""
import logging
from logging.handlers import RotatingFileHandler

import pytest

from app.services import audit
from tests.test_bot import (  # noqa: F401  env — фикстура
    ADMIN,
    buttons,
    cb,
    create_quest,
    env,
    get_user,
    make_teacher,
    msg,
    register,
    submit_text,
)
from tests.test_coins import operate
from tests.test_shop import buy, create_reward, reward_id


def payloads(answer) -> list[str]:
    return [b["payload"] for b in buttons(answer["attachments"])]


@pytest.fixture
def audit_file(tmp_path):
    path = tmp_path / "audit.log"
    audit.setup(str(path))
    yield path
    for h in list(audit.log.handlers):
        if isinstance(h, RotatingFileHandler):
            audit.log.removeHandler(h)
            h.close()


async def test_coin_feed(env):
    bot, client, sessions = env
    await make_teacher(bot, 7)
    await create_quest(bot, 7, title="Отчёт", kind="photo")
    await register(bot, 5, "Иванов Иван")
    sub = await submit_text(bot, sessions, 5, "Готово", title="Отчёт")
    await bot.handle_update(cb(7, f"rev:ok:{sub.id}"))
    student = await get_user(sessions, 5)
    await operate(bot, ADMIN, student.id, 200, reason="За помощь на субботнике")
    await create_reward(bot, price="100")
    await buy(bot, 5, await reward_id(sessions))

    await bot.handle_update(cb(ADMIN, "menu:stats"))
    assert "log:coins:0" in payloads(client.answers[-1])
    await bot.handle_update(cb(ADMIN, "log:coins:0"))
    text = client.answers[-1]["text"]
    lines = text.splitlines()
    assert "Движение монет" in text
    assert any("Иванов Иван (Тестовый класс): +50 — «Отчёт», засчитал(а) Учителева Анна" in l for l in lines)
    assert any("+200 — За помощь на субботнике, от Админов Админ" in l for l in lines)
    assert any("−100 — покупка «Пропуск без очереди»" in l for l in lines)
    # новые сверху: покупка — первой
    events = [l for l in lines if "Иванов Иван" in l]
    assert "покупка" in events[0]


async def test_staff_feed_and_access(env, audit_file):
    bot, client, sessions = env
    await make_teacher(bot, 7)
    await register(bot, 30, "Модеров Максим")
    await bot.handle_update(msg(ADMIN, "/role 30 moderator"))  # пишется в журнал
    await bot.handle_update(cb(ADMIN, "log:staff:0"))
    text = client.answers[-1]["text"]
    assert "Действия админа и модераторов" in text
    assert "Админов Админ (админ) — роль: Модеров Максим (max 30) → moderator" in text

    # модератор видит кнопки с замком, но открыть журналы не может
    await bot.handle_update(cb(30, "menu:stats"))
    titles = [b["text"] for b in buttons(client.answers[-1]["attachments"])]
    assert "🔒 Движение монет" in titles and "🔒 Действия персонала" in titles
    for payload in ("log:coins:0", "log:staff:0"):
        await bot.handle_update(cb(30, payload))
        assert "только админу" in client.answers[-1]["notification"]


async def test_coin_feed_pages(env):
    bot, client, sessions = env
    await register(bot, ADMIN, "Админов Админ")
    await register(bot, 5)
    student = await get_user(sessions, 5)
    for i in range(20):
        await operate(bot, ADMIN, student.id, 1 + i, reason=f"Начисление {i}")
    await bot.handle_update(cb(ADMIN, "log:coins:0"))
    answer = client.answers[-1]
    assert "Начисление 19" in answer["text"] and "log:coins:1" in payloads(answer)
    await bot.handle_update(cb(ADMIN, "log:coins:1"))
    answer = client.answers[-1]
    assert "Страница 2" in answer["text"] and "Начисление 0" in answer["text"]
    assert "log:coins:0" in payloads(answer)
