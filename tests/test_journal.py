"""Журнал успехов: учителя отмечают успехи любых учеников, записи копятся в карточке."""
from sqlalchemy import select

from app.db.models import CoinTransaction, Success
from tests.test_bot import (  # noqa: F401  env — фикстура
    ADMIN,
    add_class,
    buttons,
    cb,
    env,
    get_user,
    make_teacher,
    msg,
    register,
)

OTHER_TEACHER = 8
MOD = 30


def payloads(attachments) -> list[str]:
    return [b["payload"] for b in buttons(attachments)]


async def setup(bot, sessions):
    """Учитель 7 — Тестовый класс (ученик 5); ученик 6 — в 5Б без учителя; учитель 8 без классов."""
    await make_teacher(bot, 7)
    await add_class(sessions, "5Б")
    await register(bot, 5)
    await register(bot, 6, "Петрова Мария", school_class="5Б")
    await register(bot, OTHER_TEACHER, "Другая Учительница")
    await bot.handle_update(msg(ADMIN, f"/role {OTHER_TEACHER} teacher"))
    await register(bot, MOD, "Модеров Максим")
    await bot.handle_update(msg(ADMIN, f"/role {MOD} moderator"))
    return (await get_user(sessions, 5)).id, (await get_user(sessions, 6)).id


async def write(bot, uid, student_id, category, text, coins=None):
    await bot.handle_update(cb(uid, f"jr:ns:{student_id}"))
    await bot.handle_update(cb(uid, f"jr:cat:{category}"))
    await bot.handle_update(msg(uid, text))
    if coins is not None:
        await bot.handle_update(cb(uid, f"jr:coins:{coins}"))


async def test_any_teacher_notes_any_student_without_coins(env):
    bot, client, sessions = env
    _, other_student = await setup(bot, sessions)
    await bot.handle_update(cb(OTHER_TEACHER, "menu:journal"))
    assert "jr:new" in payloads(client.answers[-1]["attachments"])
    client.sent.clear()
    await write(bot, OTHER_TEACHER, other_student, "sport", "Первое место в школьной эстафете")
    # монеты чужому ученику не предлагаются — запись сохраняется сразу
    confirm = [t for to, t, _ in client.sent if to == OTHER_TEACHER][-1]
    assert "Успех записан: Петрова Мария" in confirm and "учитель класса" in confirm
    notice = [t for to, t, _ in client.sent if to == 6][-1]
    assert "Другая Учительница отметил(а) ваш успех" in notice and "⚽ Спорт" in notice
    async with sessions() as s:
        success = await s.scalar(select(Success))
    assert (success.category, success.coins, success.author_role.value) == ("sport", 0, "teacher")


async def test_own_class_with_coins(env):
    bot, client, sessions = env
    student, _ = await setup(bot, sessions)
    await bot.handle_update(cb(7, f"jr:ns:{student}"))
    await bot.handle_update(cb(7, "jr:cat:olymp"))
    await bot.handle_update(msg(7, "Призёр районной олимпиады по математике"))
    coin_buttons = payloads(client.sent[-1][2])
    assert coin_buttons[:5] == ["jr:coins:0", "jr:coins:20", "jr:coins:50", "jr:coins:100", "jr:coins:150"]
    await bot.handle_update(cb(7, "jr:coins:999"))  # подделанная сумма
    assert "кнопкой" in client.answers[-1]["notification"]
    await bot.handle_update(cb(7, "jr:coins:100"))
    assert "начислено 100 монет" in client.answers[-1]["text"]
    assert (await get_user(sessions, 5)).balance == 100
    async with sessions() as s:
        tx = await s.scalar(select(CoinTransaction))
    assert tx.reason == "🌟 Успех: Призёр районной олимпиады по математике"
    notice = [t for to, t, _ in client.sent if to == 5][-1]
    assert "+100 монет" in notice


async def test_card_class_and_feed(env):
    bot, client, sessions = env
    student, _ = await setup(bot, sessions)
    await write(bot, 7, student, "study", "Отличная проектная работа", coins=0)
    await write(bot, OTHER_TEACHER, student, "art", "Рисунок на выставку")

    await bot.handle_update(cb(OTHER_TEACHER, f"jr:st:{student}:0"))
    card = client.answers[-1]
    assert "🌟 Успехов: 2" in card["text"] and "📚 1 · 🎨 1" in card["text"]
    entries = [p for p in payloads(card["attachments"]) if p.startswith("jr:e:")]
    assert len(entries) == 2

    await bot.handle_update(cb(OTHER_TEACHER, entries[0]))  # новые сверху
    assert "Рисунок на выставку" in client.answers[-1]["text"]
    assert "— Другая Учительница, учитель" in client.answers[-1]["text"]

    async with sessions() as s:
        class_id = (await get_user(sessions, 5)).class_id
    await bot.handle_update(cb(ADMIN, f"jr:all:{class_id}:0"))
    answer = client.answers[-1]
    assert "Успехов за всё время: 2" in answer["text"]
    assert any(b["text"].endswith("· 🌟2 · 🎯0") for b in buttons(answer["attachments"]))

    await bot.handle_update(cb(MOD, "jr:feed:0"))
    assert client.answers[-1]["text"].count("Иванов Иван") == 2


async def test_rights(env):
    bot, client, sessions = env
    student, _ = await setup(bot, sessions)
    # модератор смотрит, но не пишет
    await bot.handle_update(cb(MOD, "menu:journal"))
    assert "jr:new" not in payloads(client.answers[-1]["attachments"])
    await bot.handle_update(cb(MOD, f"jr:ns:{student}"))
    assert "учителя и администрация" in client.answers[-1]["notification"]
    # ученик не видит журнал
    await bot.handle_update(cb(5, "menu:journal"))
    assert "доступен учителям" in client.answers[-1]["notification"]


async def test_delete_by_author_or_admin(env):
    bot, client, sessions = env
    student, _ = await setup(bot, sessions)
    await write(bot, 7, student, "social", "Организовал субботник", coins=0)
    async with sessions() as s:
        success_id = (await s.scalar(select(Success))).id
    await bot.handle_update(cb(OTHER_TEACHER, f"jr:e:{success_id}"))
    assert f"jr:del:{success_id}" not in payloads(client.answers[-1]["attachments"])
    await bot.handle_update(cb(OTHER_TEACHER, f"jr:delok:{success_id}"))
    assert "автор или администратор" in client.answers[-1]["notification"]
    await bot.handle_update(cb(7, f"jr:del:{success_id}"))
    await bot.handle_update(cb(7, f"jr:delok:{success_id}"))
    assert "Записей об успехах пока нет" in client.answers[-1]["text"]


async def test_student_sees_own_successes(env):
    bot, client, sessions = env
    student, _ = await setup(bot, sessions)
    await bot.handle_update(cb(5, "jr:my:0"))
    assert "Пока записей нет" in client.answers[-1]["text"]
    await write(bot, 7, student, "other", "Помог однокласснику разобраться с задачей", coins=20)
    await bot.handle_update(cb(5, "menu:profile"))
    assert "jr:my:0" in payloads(client.answers[-1]["attachments"])
    await bot.handle_update(cb(5, "jr:my:0"))
    text = client.answers[-1]["text"]
    assert "🌟 Мои успехи: 1" in text and "Помог однокласснику" in text
    assert "— Учителева Анна, учитель" in text and "+20 монет" in text


async def test_cancel_and_text_rules(env):
    bot, client, sessions = env
    student, _ = await setup(bot, sessions)
    await bot.handle_update(cb(7, f"jr:ns:{student}"))
    await bot.handle_update(cb(7, "jr:cat:study"))
    await bot.handle_update(msg(7, "х" * 501))
    assert "максимум 500" in client.sent[-1][1]
    await bot.handle_update(cb(7, "cancel"))
    await bot.handle_update(cb(7, "jr:coins:0"))
    assert "уже сохранена или отменена" in client.answers[-1]["notification"]
    async with sessions() as s:
        assert await s.scalar(select(Success)) is None
