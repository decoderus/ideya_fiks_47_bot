"""Справка «ℹ️ Помощь»: первой кнопкой до первого просмотра, потом — последней."""
from app.help_texts import TEXTS
from tests.test_bot import (  # noqa: F401  env — фикстура
    ADMIN,
    cb,
    env,
    get_user,
    msg,
    register,
)


def menu_payloads(attachments) -> list[str]:
    return [b["payload"] for row in attachments[0]["payload"]["buttons"] for b in row]


async def test_help_first_then_last(env):
    bot, client, sessions = env
    await register(bot, 5)
    await bot.handle_update(msg(5, "/start"))
    payloads = menu_payloads(client.sent[-1][2])
    assert payloads[0] == "menu:help"

    await bot.handle_update(cb(5, "menu:help"))
    answer = client.answers[-1]
    assert answer["text"] == TEXTS[(await get_user(sessions, 5)).role]
    assert "🎯 Квесты" in answer["text"] and "menu:main" in menu_payloads(answer["attachments"])
    assert (await get_user(sessions, 5)).help_seen

    await bot.handle_update(cb(5, "menu:main"))
    payloads = menu_payloads(client.answers[-1]["attachments"])
    assert payloads[-1] == "menu:help" and payloads[0] != "menu:help"


async def test_help_command_per_role(env):
    bot, client, sessions = env
    await register(bot, ADMIN, "Админов Админ")
    await bot.handle_update(msg(ADMIN, "/help"))
    text = client.sent[-1][1]
    assert text.startswith("ℹ️ Справка для администратора") and "/modreview" in text


def test_help_texts_cover_menus():
    """В справке есть каждый доступный раздел меню роли."""
    from app import keyboards as kb

    for role, text in TEXTS.items():
        for title, payload in kb.MENUS[role]:
            if payload in ("menu:restart",) or title.startswith("🔒"):
                continue
            name = title.split(" ", 1)[1]
            assert name in text, (role, title)
