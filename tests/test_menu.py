"""Упрощённое меню: «Квесты» с созданием внутри, «Школа», выдача в «Наградах»,
регистрации в «Статистике», счётчики того, что ждёт действия."""
from tests.test_bot import ADMIN, cb, env, msg, register  # noqa: F401  env — фикстура
from tests.test_moderator import MOD
from tests.test_moderator import school as school_with_moderator
from tests.test_shop import buy, create_reward, reward_id


def buttons_of(attachments) -> list[tuple[str, str]]:
    return [(b["text"], b["payload"]) for row in attachments[0]["payload"]["buttons"] for b in row]


def payloads(attachments) -> list[str]:
    return [p for _, p in buttons_of(attachments)]


async def menu(bot, client, uid) -> list[tuple[str, str]]:
    await bot.handle_update(msg(uid, "/start"))
    return buttons_of(client.sent[-1][2])


async def test_admin_menu_is_shorter(env, monkeypatch):
    bot, client, sessions = env
    await school_with_moderator(bot, sessions)
    items = await menu(bot, client, ADMIN)
    got = [p for _, p in items]
    for gone in ("menu:new_quest", "ord:list", "enl:0", "menu:classes", "menu:teachers"):
        assert gone not in got, gone
    assert {"mq:list:a:0", "menu:school", "menu:rewards", "menu:stats"} <= set(got)
    assert len(items) == 14  # 13 разделов + «Помощь»
    assert ("📂 Квесты", "mq:list:a:0") in items
    mod = [p for _, p in await menu(bot, client, MOD)]
    assert "menu:school" in mod and "menu:new_quest" not in mod


async def test_teacher_menu(env):
    bot, client, sessions = env
    await school_with_moderator(bot, sessions)
    got = [p for _, p in await menu(bot, client, 7)]
    assert "mq:list:a:0" in got and "menu:new_quest" not in got
    assert "ord:list" in got  # у учителя выдача остаётся в меню
    assert "menu:school" not in got and "menu:classes" in got


async def test_new_quest_inside_quests(env):
    bot, client, sessions = env
    await school_with_moderator(bot, sessions)
    for uid in (ADMIN, MOD, 7):
        await bot.handle_update(cb(uid, "mq:list:a:0"))
        assert payloads(client.answers[-1]["attachments"])[0] == "menu:new_quest", uid
    await bot.handle_update(cb(7, "menu:new_quest"))
    assert "Новый квест. Шаг 1" in client.answers[-1]["text"]


async def test_school_section(env):
    bot, client, sessions = env
    await school_with_moderator(bot, sessions)
    await bot.handle_update(cb(ADMIN, "menu:school"))
    assert payloads(client.answers[-1]["attachments"])[:2] == ["menu:classes", "menu:teachers"]
    await bot.handle_update(cb(ADMIN, "menu:classes"))
    assert ("« Школа", "menu:school") in buttons_of(client.answers[-1]["attachments"])
    await bot.handle_update(cb(ADMIN, "menu:teachers"))
    assert ("« Школа", "menu:school") in buttons_of(client.answers[-1]["attachments"])
    await bot.handle_update(cb(7, "menu:school"))
    assert "админам и модераторам" in client.answers[-1]["notification"]
    await bot.handle_update(cb(7, "menu:classes"))  # «Мои классы» учителя — назад в меню
    assert "menu:school" not in payloads(client.answers[-1]["attachments"])


async def test_orders_counter_and_inside_rewards(env):
    bot, client, sessions = env
    await school_with_moderator(bot, sessions)
    await create_reward(bot, title="Сок", price="50")
    rid = await reward_id(sessions, "Сок")
    await buy(bot, 5, rid)
    await buy(bot, 5, rid)

    items = await menu(bot, client, ADMIN)
    assert ("🎁 Награды · 2", "menu:rewards") in items
    teacher = await menu(bot, client, 7)
    assert ("📦 Выдача · 2", "ord:list") in teacher

    await bot.handle_update(cb(ADMIN, "menu:rewards"))
    assert buttons_of(client.answers[-1]["attachments"])[0] == ("📦 Выдача · 2", "ord:list")
    await bot.handle_update(cb(ADMIN, "ord:list"))
    assert ("« К наградам", "menu:rewards") in buttons_of(client.answers[-1]["attachments"])
    await bot.handle_update(cb(MOD, "menu:rewards"))  # модератор тоже выдаёт
    assert buttons_of(client.answers[-1]["attachments"])[0][1] == "ord:list"


async def test_registrations_counter_inside_stats(env):
    bot, client, sessions = env
    await school_with_moderator(bot, sessions)  # учитель, ученик и модератор регистрировались как ученики
    items = await menu(bot, client, ADMIN)
    assert ("📊 Статистика · 3", "menu:stats") in items
    await bot.handle_update(cb(ADMIN, "menu:stats"))
    assert buttons_of(client.answers[-1]["attachments"])[0] == ("🆕 Регистрации · 3", "enl:0")
    await bot.handle_update(cb(ADMIN, "enl:0"))
    assert ("📊 К статистике", "menu:stats") in buttons_of(client.answers[-1]["attachments"])
    await bot.handle_update(cb(ADMIN, "enl:all"))
    items = await menu(bot, client, ADMIN)
    assert ("📊 Статистика", "menu:stats") in items  # всё проверено — без числа
