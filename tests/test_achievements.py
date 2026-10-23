"""Достижения: автоматические (квесты, монеты, покупки, серии) и выдаваемые админом."""
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.bot import Bot
from app.db.models import Achievement, AchievementKind, Submission, SubmissionStatus, User
from app.services import achievements as ach_service
from tests.test_bot import (
    ADMIN,
    FakeClient,
    TEST_CLASS,
    buttons,
    cb,
    create_quest,
    get_user,
    make_teacher,
    msg,
    quest_id,
    register,
)
from tests.test_bot import env as plain_env  # noqa: F401
from tests.test_coins import operate
from tests.test_shop import buy, create_reward, reward_id


@pytest.fixture
async def env(plain_env, monkeypatch):  # noqa: F811
    """Тот же бот, но с автодостижениями; старт учёта — в прошлом."""
    monkeypatch.setattr(ach_service, "START", datetime(2020, 1, 1, tzinfo=timezone.utc))
    bot, client, sessions = plain_env
    bot = Bot(client, sessions, admin_ids={ADMIN}, background=False)
    return bot, client, sessions


def payloads(answer) -> list[str]:
    return [b["payload"] for b in buttons(answer["attachments"])]


def notices(client, uid) -> list[str]:
    return [text for to, text, _ in client.sent if to == uid and "Новое достижение" in text]


async def achievement(sessions, title) -> Achievement:
    async with sessions() as s:
        return await s.scalar(select(Achievement).where(Achievement.title == title))


async def test_first_quest_achievement(env):
    bot, client, sessions = env
    await make_teacher(bot, 7)
    await create_quest(bot, 7)
    await register(bot, 5)
    await bot.handle_update(cb(5, f"q:done:{await quest_id(sessions)}"))

    assert len(notices(client, 5)) == 1 and "🎯 Первый шаг" in notices(client, 5)[0]
    assert (await get_user(sessions, 5)).balance == 50 + 20  # квест + бонус

    await bot.handle_update(cb(5, "menu:achievements"))
    text = client.answers[-1]["text"]
    assert "Мои достижения: 1 из 8" in text
    assert "🎯 Первый шаг ·" in text
    assert "🔒 ⭐ Десятка — выполнить 10 квестов (1 из 10)" in text
    assert "🔒 🪙 Первая тысяча — заработать 1 000 монет (70 из 1000)" in text

    # бонус виден в истории баланса
    student = await get_user(sessions, 5)
    await bot.handle_update(cb(ADMIN, f"cls:hist:{student.id}:0"))
    assert "🪙 +20 · Достижение «Первый шаг»" in client.answers[-1]["text"]


async def test_coins_and_purchase_achievements(env):
    bot, client, sessions = env
    await make_teacher(bot, 7)
    await register(bot, 5)
    student = await get_user(sessions, 5)
    await operate(bot, ADMIN, student.id, 1000)
    assert any("Первая тысяча" in n for n in notices(client, 5))
    assert (await get_user(sessions, 5)).balance == 1100

    await create_reward(bot, price="50")
    await buy(bot, 5, await reward_id(sessions))
    assert any("Первая покупка" in n for n in notices(client, 5))
    assert (await get_user(sessions, 5)).balance == 1100 - 50 + 20


async def test_daily_streak(env):
    bot, client, sessions = env
    await make_teacher(bot, 7)
    await create_quest(bot, 7, title="Зарядка", repeat="daily")
    await register(bot, 5)
    qid = await quest_id(sessions, "Зарядка")
    student = await get_user(sessions, 5)

    # серии появились сами
    await bot.handle_update(cb(5, "menu:achievements"))
    text = client.answers[-1]["text"]
    assert "🔒 🔥 Зарядка: неделя подряд" in text and "(0 из 7)" in text
    assert "🔒 🏆 Зарядка: месяц подряд" in text

    # шесть прошлых дней подряд
    async with sessions() as s:
        for days_ago in range(1, 7):
            sub = Submission(quest_id=qid, user_id=student.id, status=SubmissionStatus.approved)
            s.add(sub)
            await s.flush()
            sub.created_at = sub.created_at - timedelta(days=days_ago)
        await s.commit()
    await bot.handle_update(cb(5, "menu:achievements"))
    assert "(6 из 7)" in client.answers[-1]["text"]

    await bot.handle_update(cb(5, f"q:done:{qid}"))  # седьмой день
    assert any("🔥 Зарядка: неделя подряд" in n for n in notices(client, 5))
    await bot.handle_update(cb(5, "menu:achievements"))
    assert "(7 из 30)" in client.answers[-1]["text"]


async def test_activity_before_start_does_not_count(env, monkeypatch):
    bot, client, sessions = env
    await make_teacher(bot, 7)
    await create_quest(bot, 7)
    await create_quest(bot, 7, title="Второй")
    await register(bot, 5)
    # учёт начинается «завтра»: сегодняшний квест не в зачёт
    monkeypatch.setattr(
        ach_service, "START", datetime.now(timezone.utc) + timedelta(days=1)
    )
    await bot.handle_update(cb(5, f"q:done:{await quest_id(sessions)}"))
    assert notices(client, 5) == []
    assert (await get_user(sessions, 5)).balance == 50
    # учёт начался: старое не считается, новый квест — первый
    monkeypatch.setattr(ach_service, "START", datetime.now(timezone.utc))
    await bot.handle_update(cb(5, "menu:achievements"))
    assert "(0 из 10)" in client.answers[-1]["text"]
    await bot.handle_update(cb(5, f"q:done:{await quest_id(sessions, 'Второй')}"))
    assert any("Первый шаг" in n for n in notices(client, 5))


def test_streak_counting():
    today = date(2026, 10, 20)
    days = {today - timedelta(days=i) for i in (1, 2, 3)} | {date(2026, 10, 1), date(2026, 10, 2)}
    # сегодня ещё не выполнено — текущая серия не прервана
    assert ach_service._streaks(days, today) == (3, 3)
    assert ach_service._streaks(days | {today}, today) == (4, 4)
    assert ach_service._streaks({today - timedelta(days=2)}, today) == (1, 0)
    assert ach_service._streaks(set(), today) == (0, 0)


async def test_admin_creates_grants_and_revokes(env):
    bot, client, sessions = env
    await make_teacher(bot, 7)
    await register(bot, 5)
    student = await get_user(sessions, 5)

    await bot.handle_update(cb(ADMIN, "menu:achievements"))
    assert "ach:new" in payloads(client.answers[-1])
    await bot.handle_update(cb(ADMIN, "ach:new"))
    await bot.handle_update(msg(ADMIN, "Лучший художник"))
    await bot.handle_update(msg(ADMIN, "Победа в конкурсе рисунков"))
    await bot.handle_update(msg(ADMIN, "🎨"))
    await bot.handle_update(msg(ADMIN, "много"))
    assert "целым числом" in client.sent[-1][1]
    await bot.handle_update(msg(ADMIN, "200"))
    assert "Достижение создано" in client.sent[-1][1]
    a = await achievement(sessions, "Лучший художник")
    assert (a.kind, a.emoji, a.reward) == (AchievementKind.manual, "🎨", 200)

    # невыданное достижение админа ученик не видит
    await bot.handle_update(cb(5, "menu:achievements"))
    assert "Лучший художник" not in client.answers[-1]["text"]

    await bot.handle_update(cb(ADMIN, f"ach:gcls:{a.id}"))
    await bot.handle_update(cb(ADMIN, f"ach:give:{a.id}:{student.id}"))
    assert "получил(а) «Лучший художник»" in client.answers[-1]["text"]
    assert any("🎨 Лучший художник" in n and "+200" in n for n in notices(client, 5))
    assert (await get_user(sessions, 5)).balance == 200
    await bot.handle_update(cb(5, "menu:achievements"))
    assert "🎨 Лучший художник ·" in client.answers[-1]["text"]

    # повторное нажатие — предложение отозвать
    await bot.handle_update(cb(ADMIN, f"ach:give:{a.id}:{student.id}"))
    assert "Отозвать?" in client.answers[-1]["text"]
    await bot.handle_update(cb(ADMIN, f"ach:rev_ok:{a.id}:{student.id}"))
    assert "списано 200 монет" in client.answers[-1]["text"]
    assert (await get_user(sessions, 5)).balance == 0


async def test_admin_changes_bonus_and_access(env):
    bot, client, sessions = env
    await make_teacher(bot, 7)
    await register(bot, 5)
    await bot.handle_update(cb(ADMIN, "menu:achievements"))
    first = await achievement(sessions, "Первый шаг")
    await bot.handle_update(cb(ADMIN, f"ach:reward:{first.id}"))
    await bot.handle_update(msg(ADMIN, "0"))
    assert "Бонус теперь 0 монет" in client.sent[-1][1]
    # автодостижение вручную не выдаётся
    student = await get_user(sessions, 5)
    await bot.handle_update(cb(ADMIN, f"ach:give:{first.id}:{student.id}"))
    assert "выдаются сами" in client.answers[-1]["notification"]
    # учителю и ученику управление недоступно
    for uid in (7, 5):
        await bot.handle_update(cb(uid, "ach:new"))
        assert "только админу" in client.answers[-1]["notification"]


# --- профиль ученика ---


async def test_profile(env):
    bot, client, sessions = env
    await make_teacher(bot, 7)
    await create_quest(bot, 7, title="Зарядка", repeat="daily")
    await create_quest(bot, 7, title="Сочинение", kind="photo")
    await register(bot, 5, "Иванов Иван")
    await register(bot, 6, "Петров Пётр")
    zid = await quest_id(sessions, "Зарядка")
    student = await get_user(sessions, 5)
    # вчера и сегодня — серия 2 дня
    async with sessions() as s:
        sub = Submission(quest_id=zid, user_id=student.id, status=SubmissionStatus.approved)
        s.add(sub)
        await s.flush()
        sub.created_at = sub.created_at - timedelta(days=1)
        await s.commit()
    await bot.handle_update(cb(5, f"q:done:{zid}"))
    await bot.handle_update(cb(5, f"q:photo:{await quest_id(sessions, 'Сочинение')}"))
    await bot.handle_update(msg(5, "Моё сочинение"))

    await bot.handle_update(cb(5, "menu:profile"))
    text = client.answers[-1]["text"]
    assert "👤 Иванов Иван" in text and "Класс: Тестовый класс" in text
    assert "💰 Баланс: 70 монет" in text  # 50 за зарядку + 20 за «Первый шаг»
    assert "🏆 Место в школе: 1 из 2" in text and "👥 Место в классе: 1 из 2" in text
    assert "🎯 Квестов выполнено: 2" in text
    assert "⏳ Ждут проверки: 1" in text
    assert "• Зарядка — 2 дня подряд (сегодня ✅)" in text
    assert "🏅 Достижения: 1 из" in text and "🎯 Первый шаг ·" in text
    assert "menu:achievements" in payloads(client.answers[-1])


async def test_profile_only_for_students(env):
    bot, client, sessions = env
    await make_teacher(bot, 7)
    await bot.handle_update(cb(7, "menu:profile"))
    assert "только у учеников" in client.answers[-1]["notification"]


def test_days_word():
    from app.ui import days_word

    assert [days_word(n) for n in (1, 2, 5, 11, 21, 22, 25, 112)] == [
        "день", "дня", "дней", "дней", "день", "дня", "дней", "дней"
    ]
