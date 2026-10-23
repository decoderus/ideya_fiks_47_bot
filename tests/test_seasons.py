"""Сезоны: очки за четверть, итоги, достижения призёрам, управление датами."""
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from app.contests import morning
from app.db.models import (
    Quest,
    QuestKind,
    Season,
    SeasonResult,
    Submission,
    SubmissionStatus,
    UserAchievement,
)
from app.services import seasons as seasons_service
from tests.test_bot import (  # noqa: F401  env — фикстура
    ADMIN,
    add_class,
    cb,
    env,
    get_user,
    msg,
    register,
)

MSK = timezone(timedelta(hours=3))


def at(d: date, hour: int = 12) -> datetime:
    return datetime(d.year, d.month, d.day, hour, tzinfo=MSK)


async def make_seasons(sessions):
    async with sessions() as s:
        s.add(Season(title="I четверть 2026/27", start_date=date(2026, 9, 1)))
        s.add(Season(title="II четверть 2026/27", start_date=date(2026, 11, 3)))
        await s.commit()


async def earn(sessions, uid, amount, when: datetime):
    student = await get_user(sessions, uid)
    async with sessions() as s:
        quest = Quest(title=f"Q{amount}", reward=amount, kind=QuestKind.auto, author_id=student.id)
        s.add(quest)
        await s.flush()
        s.add(Submission(quest_id=quest.id, user_id=student.id,
                         status=SubmissionStatus.approved, created_at=when))
        (await s.get(type(student), student.id)).balance += amount
        await s.commit()


def set_today(monkeypatch, d: date):
    monkeypatch.setattr(seasons_service, "today", lambda: d)


async def test_rating_by_current_balance(env, monkeypatch):
    bot, client, sessions = env
    await make_seasons(sessions)
    set_today(monkeypatch, date(2026, 11, 10))
    await register(bot, 1, "Альфа Аня")
    await register(bot, 2, "Бета Боря")
    await earn(sessions, 1, 1000, at(date(2026, 10, 15)))  # прошлый сезон
    await earn(sessions, 2, 50, at(date(2026, 11, 5)))

    await bot.handle_update(cb(1, "menu:rating"))
    text = client.answers[-1]["text"]
    assert "По текущему балансу монет" in text
    # баланс не обнуляется: заработанное в прошлом сезоне остаётся в рейтинге
    assert "🥇 Альфа Аня — 1000 ← вы" in text and "🥈 Бета Боря — 50" in text

    await bot.handle_update(cb(1, "menu:profile"))
    profile = client.answers[-1]["text"]
    assert "Очки сезона" not in profile and "🏆 Место в школе: 1 из 2" in profile


async def test_close_season_results_and_prizes(env, monkeypatch):
    bot, client, sessions = env
    await make_seasons(sessions)
    other = await add_class(sessions, "5Б")
    for uid, name, amount in ((1, "Альфа Аня", 300), (2, "Бета Боря", 200), (3, "Вега Вера", 100),
                              (4, "Гамма Галя", 50)):
        await register(bot, uid, name)
        await earn(sessions, uid, amount, at(date(2026, 10, 1)))
    await register(bot, 5, "Дельта Дима", school_class="5Б")
    await earn(sessions, 5, 80, at(date(2026, 10, 2)))
    await register(bot, 6, "Ноль Нина")

    # сезон ещё идёт — закрывать нечего
    set_today(monkeypatch, date(2026, 11, 2))
    async with sessions() as s:
        assert await seasons_service.close_due(s) == []

    set_today(monkeypatch, date(2026, 11, 3))
    client.sent.clear()
    await morning(bot, sessions, at(date(2026, 11, 3), 8))
    announcement = next(text for to, text, _ in client.sent if to == 1 and "Итоги сезона" in text)
    assert "🏁 Итоги сезона «I четверть 2026/27»" in announcement
    assert "🥇 Альфа Аня — 300" in announcement and "5. Дельта Дима" not in announcement
    assert "4. Дельта Дима — 80" in announcement
    assert "5Б: Дельта Дима — 80" in announcement and "Тестовый класс: Альфа Аня — 300" in announcement
    assert "Ноль Нина" not in announcement  # без очков в итоги не попадает
    notices = [text for to, text, _ in client.sent if "Новое достижение" in text]
    assert any("🥇 Чемпион: I четверть 2026/27" in t for t in notices)
    assert len(notices) == 3

    async with sessions() as s:
        assert (await s.scalar(select(Season).where(Season.start_date == date(2026, 9, 1)))).closed_at
        assert len(list(await s.scalars(select(SeasonResult)))) == 5 + 2  # топ школы + 2 класса
        assert len(list(await s.scalars(select(UserAchievement)))) == 3
    assert (await get_user(sessions, 1)).balance == 300  # бонус 0, монеты на месте

    # повторный запуск ничего не делает
    client.sent.clear()
    await morning(bot, sessions, at(date(2026, 11, 3), 9))
    assert client.sent == []

    # архив виден ученику
    async with sessions() as s:
        first = await s.scalar(select(Season).where(Season.start_date == date(2026, 9, 1)))
    await bot.handle_update(cb(6, "sz:archive"))
    assert f"sz:res:{first.id}" in str(client.answers[-1]["attachments"])
    await bot.handle_update(cb(6, f"sz:res:{first.id}"))
    assert "🥈 Бета Боря — 200" in client.answers[-1]["text"]
    assert other


async def test_default_seasons_and_past_ones_closed(env, monkeypatch):
    bot, client, sessions = env
    set_today(monkeypatch, date(2027, 2, 1))
    async with sessions() as s:
        seasons = await seasons_service.all_seasons(s)
        current = await seasons_service.current(s)
    assert [(x.title, x.start_date) for x in seasons] == [
        ("I четверть 2026/27", date(2026, 9, 1)),
        ("II четверть 2026/27", date(2026, 10, 26)),
        ("III четверть 2026/27", date(2026, 12, 31)),
        ("IV четверть 2026/27", date(2027, 3, 27)),
        ("Летний сезон 2027", date(2027, 5, 27)),
        ("I четверть 2027/28", date(2027, 9, 1)),
    ]
    assert [x.closed_at is not None for x in seasons] == [True, True, False, False, False, False]
    assert current.season.title == "III четверть 2026/27"


async def test_admin_manages_seasons(env, monkeypatch):
    bot, client, sessions = env
    await make_seasons(sessions)
    set_today(monkeypatch, date(2026, 10, 18))
    await register(bot, ADMIN, "Админов Админ")
    await register(bot, 5)

    await bot.handle_update(cb(ADMIN, "menu:seasons"))
    text = client.answers[-1]["text"]
    assert "I четверть 2026/27 — с 01.09.2026 ← идёт" in text

    await bot.handle_update(cb(ADMIN, "sz:add"))
    await bot.handle_update(msg(ADMIN, "III четверть 2026/27"))
    await bot.handle_update(msg(ADMIN, "12.13.2027"))
    assert "Такой даты нет" in client.sent[-1][1]
    await bot.handle_update(msg(ADMIN, "01.10.2026"))
    assert "должна быть в будущем" in client.sent[-1][1]
    await bot.handle_update(msg(ADMIN, "12.01.2027"))
    assert "Добавлен сезон «III четверть 2026/27»" in client.sent[-1][1]

    async with sessions() as s:
        second = await s.scalar(select(Season).where(Season.start_date == date(2026, 11, 3)))
        first = await s.scalar(select(Season).where(Season.start_date == date(2026, 9, 1)))
    await bot.handle_update(cb(ADMIN, f"sz:move:{second.id}"))
    await bot.handle_update(msg(ADMIN, "05.11.2026"))
    assert "начнётся 05.11.2026" in client.sent[-1][1]
    # начавшийся сезон не переносится и не удаляется
    await bot.handle_update(cb(ADMIN, f"sz:open:{first.id}"))
    assert f"sz:del:{first.id}" not in str(client.answers[-1]["attachments"])
    await bot.handle_update(cb(ADMIN, f"sz:del:{first.id}"))
    assert "удалить нельзя" in client.answers[-1]["notification"]
    await bot.handle_update(cb(ADMIN, f"sz:del:{second.id}"))
    assert "удалён" in client.answers[-1]["text"]
    # ученику управление недоступно
    await bot.handle_update(cb(5, "menu:seasons"))
    assert "только админам" in client.answers[-1]["notification"]
