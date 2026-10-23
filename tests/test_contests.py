"""Соревнование классов: подсчёт, допуск, номинации, итоги."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.contests import announcement
from app.db.models import (
    ClassContestResult,
    CoinTransaction,
    ContestCriterion,
    ContestPeriod,
    Quest,
    QuestKind,
    Role,
    SchoolClass,
    Submission,
    SubmissionStatus,
    User,
)
from app.services import contests as contests_service
from tests.test_bot import cb, env, register  # noqa: F401  env — фикстура

MSK = timezone(timedelta(hours=3))
MONDAY = datetime(2026, 10, 19, 8, 0, tzinfo=MSK)  # итоги недели 12.10–18.10
LAST_WEEK = datetime(2026, 10, 14, 12, 0, tzinfo=MSK)
uid_counter = iter(range(1000, 100000))


async def make_class(sessions, name, students, active, reward, when=LAST_WEEK) -> int:
    """Класс из students учеников; первые active выполнили квест на reward монет."""
    async with sessions() as s:
        school_class = SchoolClass(name=name)
        s.add(school_class)
        await s.flush()
        author = User(max_user_id=next(uid_counter), name="T", full_name="Учитель", role=Role.admin)
        s.add(author)
        await s.flush()
        quest = Quest(title=f"Квест {name}", reward=reward, kind=QuestKind.auto, author_id=author.id)
        s.add(quest)
        await s.flush()
        for i in range(students):
            u = User(
                max_user_id=next(uid_counter), name="У", full_name=f"{name} ученик {i}",
                role=Role.student, class_id=school_class.id,
            )
            s.add(u)
            await s.flush()
            if i < active:
                s.add(Submission(
                    quest_id=quest.id, user_id=u.id, status=SubmissionStatus.approved,
                    created_at=when,
                ))
        await s.commit()
        return school_class.id


async def summarize(sessions, period=ContestPeriod.week, now=MONDAY):
    async with sessions() as s:
        return await contests_service.summarize(s, period, now)


def names(summary, criterion):
    return [c.name for c in summary.winners[criterion]]


async def test_two_criteria_and_eligibility(env):
    bot, client, sessions = env
    await make_class(sessions, "10Б", students=10, active=5, reward=40)  # 200, по 20
    await make_class(sessions, "5А", students=5, active=5, reward=30)  # 150, по 30
    await make_class(sessions, "11А", students=4, active=4, reward=1000)  # мало участников
    await make_class(sessions, "7В", students=20, active=5, reward=5)  # 25, по 1

    summary = await summarize(sessions)
    assert names(summary, ContestCriterion.total) == ["10Б", "5А", "7В"]
    assert names(summary, ContestCriterion.average) == ["5А", "10Б", "7В"]
    assert (summary.start.isoformat(), summary.end.isoformat()) == ("2026-10-12", "2026-10-18")

    text = announcement(summary)
    assert "Итоги недели" in text and "(12.10–18.10)" in text
    assert "🥇 10Б — 200 монет" in text and "🥇 5А — 30 на ученика" in text
    assert "11А" not in text

    # повторный запуск за тот же период ничего не делает
    assert await summarize(sessions) is None


async def test_only_period_activity_counts(env):
    bot, client, sessions = env
    old = LAST_WEEK - timedelta(days=7)
    cid = await make_class(sessions, "6А", students=5, active=5, reward=10, when=old)
    summary = await summarize(sessions)
    assert summary.winners[ContestCriterion.total] == []
    assert "ни один класс не набрал 5 активных" in announcement(summary)

    # начисления учителя считаются в сумму, но участником делает только квест
    async with sessions() as s:
        student = (await s.scalars(select(User).where(User.class_id == cid))).first()
        s.add(CoinTransaction(
            user_id=student.id, actor_id=student.id, amount=500, reason="x", created_at=LAST_WEEK
        ))
        await s.commit()
        items = await contests_service.scores(
            s, *contests_service.previous_bounds(ContestPeriod.week, MONDAY)
        )
    six_a = next(c for c in items if c.name == "6А")
    assert (six_a.total, six_a.active, six_a.eligible) == (500, 0, False)


async def test_month_bounds():
    start, end = contests_service.previous_bounds(
        ContestPeriod.month, datetime(2026, 11, 1, 8, 0, tzinfo=MSK)
    )
    assert start.astimezone(MSK) == datetime(2026, 10, 1, tzinfo=MSK)
    assert end.astimezone(MSK) == datetime(2026, 11, 1, tzinfo=MSK)


async def test_contest_screen(env):
    bot, client, sessions = env
    await register(bot, 5)
    await bot.handle_update(cb(5, "menu:rating"))
    payloads = [b["payload"] for row in client.answers[-1]["attachments"][0]["payload"]["buttons"] for b in row]
    assert "rate:contest" in payloads
    await bot.handle_update(cb(5, "rate:contest"))
    text = client.answers[-1]["text"]
    assert "Соревнование классов" in text and "Пока ни один класс не допущен" in text
    assert "Ваш класс «Тестовый класс»" in text and "нужно ещё 5 активных" in text
    assert "Пока ни один класс не набрал нужного числа активных учеников." in text

    # допущенные классы перечислены, больше активных — выше
    now = datetime.now(timezone.utc)
    await make_class(sessions, "5А", students=6, active=5, reward=30, when=now)
    await make_class(sessions, "6Б", students=7, active=7, reward=10, when=now)
    await make_class(sessions, "7В", students=6, active=4, reward=50, when=now)
    await bot.handle_update(cb(5, "rate:contest"))
    assert "Сейчас допущены (2): 6Б (7 акт.), 5А (5 акт.)" in client.answers[-1]["text"]


async def test_rating_shows_contest_brief(env):
    bot, client, sessions = env
    await register(bot, 5)
    await bot.handle_update(cb(5, "menu:rating"))
    text = client.answers[-1]["text"]
    assert "🏁 Соревнование классов, эта неделя:" in text
    assert "💰 Лидер по сумме: пока нет" in text and "⚖️ Лидер в среднем: пока нет" in text
    assert "Ваш класс: активных 0 из 1, для участия нужно ещё 5 активных" in text

    # класс набрал участников на этой неделе — становится лидером
    now = datetime.now(timezone.utc)
    await make_class(sessions, "5А", students=5, active=5, reward=30, when=now)
    await bot.handle_update(cb(5, "menu:rating"))
    text = client.answers[-1]["text"]
    assert "💰 Лидер по сумме: 5А — 150 монет" in text
    assert "⚖️ Лидер в среднем: 5А — 30 на ученика" in text


# --- награды классам ---


async def test_rewards_badges_and_no_head_start(env):
    from app.contests import morning
    from app.db.models import Achievement, UserAchievement
    from app.services import seasons as seasons_service

    bot, client, sessions = env
    big = await make_class(sessions, "10Б", students=10, active=5, reward=40)  # сумма 1, среднее 2
    small = await make_class(sessions, "5А", students=5, active=5, reward=30)  # сумма 2, среднее 1
    third = await make_class(sessions, "7В", students=20, active=5, reward=5)  # 3 и 3

    await morning(bot, sessions, MONDAY)

    async with sessions() as s:
        def members(cid):
            return s.scalars(select(User).where(User.class_id == cid).order_by(User.id))
        big_users = list(await members(big))
        small_users = list(await members(small))
        third_users = list(await members(third))
        # одна награда на класс — за лучшее место; только активным
        assert [u.balance for u in big_users[:5]] == [250] * 5
        assert [u.balance for u in big_users[5:]] == [0] * 5
        assert [u.balance for u in small_users] == [250] * 5
        assert [u.balance for u in third_users[:5]] == [100] * 5
        team = await s.scalar(select(Achievement).where(Achievement.title == "Командная победа"))
        owners = set(await s.scalars(
            select(UserAchievement.user_id).where(UserAchievement.achievement_id == team.id)
        ))
        assert owners == {u.id for u in big_users[:5] + small_users}

        # бонус не идёт в очки — победитель не получает фору
        points, _ = await seasons_service.earnings(s, None, None)
        assert points[small_users[0].id] == 30

    notices = [t for to, t, _ in client.sent if to == small_users[0].max_user_id]
    assert any("Ваш класс «5А» — 1 место недели" in t and "+250 монет" in t for t in notices)
    assert any("Командная победа" in t for t in notices)

    # значки — в соревновании и в кнопках рейтинга
    async with sessions() as s:
        badges = await contests_service.badges(s)
    assert badges == {big: ["🏆 Класс недели"], small: ["🏆 Класс недели"]}

    # повторный запуск — без повторных наград
    await morning(bot, sessions, MONDAY + timedelta(hours=1))
    async with sessions() as s:
        assert (await s.get(User, small_users[0].id)).balance == 250


async def test_rating_shows_class_badge(env):
    bot, client, sessions = env
    await register(bot, 5)
    async with sessions() as s:
        test_class = (await s.scalars(select(SchoolClass))).first()
        s.add(ClassContestResult(
            period=ContestPeriod.week, period_start=MONDAY.date() - timedelta(days=7),
            criterion=ContestCriterion.total, place=1, class_id=test_class.id,
            class_name=test_class.name, value=100, active=5, students=5,
        ))
        await s.commit()
    await bot.handle_update(cb(5, "menu:rating"))
    answer = client.answers[-1]
    assert "Ваш класс сейчас: 🏆 Класс недели!" in answer["text"]
    titles = [b["text"] for row in answer["attachments"][0]["payload"]["buttons"] for b in row]
    assert "👥 Тестовый класс 🏆" in titles
