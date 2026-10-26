"""Опрос: приглашения только непрошедшим, прохождение, награда, кнопка в меню, итоги."""
from sqlalchemy import func, select

from app.db.models import SurveyResponse
from app.services import survey as survey_service
from tests.test_bot import (  # noqa: F401  env — фикстура
    ADMIN,
    buttons,
    cb,
    env,
    get_user,
    make_teacher,
    msg,
    register,
)


def payloads(attachments) -> list[str]:
    return [b["payload"] for b in buttons(attachments)]


def invited(client) -> set[int]:
    return {uid for uid, text, _ in client.sent if text.startswith("📋")}


async def menu_payloads(bot, client, uid) -> list[str]:
    await bot.handle_update(cb(uid, "menu:main"))
    return payloads(client.answers[-1]["attachments"])


async def pass_survey(bot, client, uid, comment: str | None = "Добавьте больше спортивных квестов"):
    await bot.handle_update(cb(uid, "sv:start"))
    user = await get_user(bot.sessions, uid)
    for i, q in enumerate(survey_service.questions_for(user)):
        assert f"вопрос {i + 1} из 4" in client.answers[-1]["text"]
        key = q.options[-1][0]
        assert f"sv:a:{i}:{key}" in payloads(client.answers[-1]["attachments"])
        await bot.handle_update(cb(uid, f"sv:a:{i}:{key}"))
    assert "Последний вопрос" in client.answers[-1]["text"]
    if comment is None:
        await bot.handle_update(cb(uid, "sv:skip"))
        return client.answers[-1]["text"]
    await bot.handle_update(msg(uid, comment))
    return client.sent[-1][1]


async def test_survey_flow(env):
    bot, client, sessions = env
    await make_teacher(bot, 7)
    await register(bot, 5, "Иванов Иван")
    await register(bot, 6, "Петрова Мария")
    assert "sv:start" not in await menu_payloads(bot, client, 5)  # опрос ещё не открыт

    client.sent.clear()
    await bot.handle_update(cb(ADMIN, "sv:send"))
    assert "Приглашения отправляются: 3" in client.answers[-1]["text"]
    assert invited(client) == {5, 6, 7}
    assert "sv:start" in await menu_payloads(bot, client, 5)

    before = (await get_user(sessions, 5)).balance
    text = await pass_survey(bot, client, 5)
    assert "Спасибо" in text and "+100" in text
    assert (await get_user(sessions, 5)).balance == before + 100
    assert "sv:start" not in await menu_payloads(bot, client, 5)  # кнопка пропала

    # учитель — без монет, пожелание можно пропустить
    teacher_before = (await get_user(sessions, 7)).balance
    text = await pass_survey(bot, client, 7, comment=None)
    assert "Спасибо" in text and "+100" not in text
    assert (await get_user(sessions, 7)).balance == teacher_before

    # повторная отправка — только тем, кто не прошёл
    client.sent.clear()
    await bot.handle_update(cb(ADMIN, "sv:send"))
    assert invited(client) == {6}

    # пройти второй раз нельзя
    await bot.handle_update(cb(5, "sv:start"))
    assert "уже прошли" in client.answers[-1]["notification"]
    async with sessions() as s:
        assert await s.scalar(select(func.count()).select_from(SurveyResponse)) == 2

    # итоги и пожелания — без имён
    await bot.handle_update(cb(ADMIN, "sv:admin"))
    text = client.answers[-1]["text"]
    assert "учеников — 1, учителей — 1" in text and "Ещё не прошли: 1" in text
    await bot.handle_update(cb(ADMIN, "sv:res:student"))
    assert "NPS: +100" in client.answers[-1]["text"]  # ответили «10»
    await bot.handle_update(cb(ADMIN, "sv:com"))
    text = client.answers[-1]["text"]
    assert "Добавьте больше спортивных квестов" in text and "Иванов" not in text

    # закрыли — кнопка пропала и у тех, кто не прошёл
    await bot.handle_update(cb(ADMIN, "sv:close"))
    assert "sv:start" not in await menu_payloads(bot, client, 6)
    await bot.handle_update(cb(6, "sv:start"))
    assert "закрыт" in client.answers[-1]["notification"]


async def test_survey_rules(env):
    bot, client, sessions = env
    await make_teacher(bot, 7)  # заодно регистрирует админа
    await register(bot, 5)
    await bot.handle_update(cb(ADMIN, "sv:send"))
    # ответы не по порядку и чужие варианты не принимаются
    await bot.handle_update(cb(5, "sv:start"))
    await bot.handle_update(cb(5, "sv:a:2:no"))
    assert "текущий вопрос" in client.answers[-1]["notification"]
    await bot.handle_update(cb(5, "sv:a:0:bogus"))
    assert "кнопкой" in client.answers[-1]["notification"]
    # ученик не видит итоги
    await bot.handle_update(cb(5, "sv:admin"))
    assert "администратор" in client.answers[-1]["notification"]
    # «Пройду позже» прерывает опрос — кнопка в меню остаётся
    await bot.handle_update(cb(5, "cancel"))
    assert "sv:start" in await menu_payloads(bot, client, 5)
    # админ сам опрос не проходит, но видит его в статистике
    assert "sv:start" not in await menu_payloads(bot, client, ADMIN)
    await bot.handle_update(cb(ADMIN, "menu:stats"))
    assert "sv:admin" in payloads(client.answers[-1]["attachments"])


def test_nps():
    assert survey_service.nps([]) is None
    assert survey_service.nps([10, 9, 8, 3]) == 25
