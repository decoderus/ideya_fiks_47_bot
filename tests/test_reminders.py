"""Утренние напоминания о сериях повторяемых квестов."""
from datetime import datetime, timedelta, timezone

from app.db.models import Quest, QuestKind, QuestRepeat, Submission, SubmissionStatus
from app.services import achievements as ach_service
from app.services import reminders as reminders_service
from tests.test_bot import (  # noqa: F401  env — фикстура
    add_class,
    env,
    get_user,
    make_teacher,
    register,
)

MSK = timezone(timedelta(hours=3))
# Понедельник, 19.10.2026, 08:00 по Москве
MONDAY = datetime(2026, 10, 19, 8, 0, tzinfo=MSK)


async def quest(sessions, title, repeat, author_id, class_id=None) -> int:
    async with sessions() as s:
        q = Quest(
            title=title, reward=10, kind=QuestKind.auto, repeat=repeat,
            author_id=author_id, class_id=class_id,
        )
        s.add(q)
        await s.commit()
        return q.id


async def done(sessions, user_id, quest_id, *moments, status=SubmissionStatus.approved):
    async with sessions() as s:
        for moment in moments:
            s.add(Submission(quest_id=quest_id, user_id=user_id, status=status, created_at=moment))
        await s.commit()


def day(d: int, hour: int = 12) -> datetime:
    return datetime(2026, 10, d, hour, 0, tzinfo=MSK)


async def collect(sessions, now=MONDAY):
    async with sessions() as s:
        return {r.student.full_name: r for r in await reminders_service.collect(s, now)}


async def test_daily_streak_reminders(env, monkeypatch):
    bot, client, sessions = env
    monkeypatch.setattr(ach_service, "START", datetime(2020, 1, 1, tzinfo=timezone.utc))
    await make_teacher(bot, 7)
    teacher = await get_user(sessions, 7)
    for uid, name in ((1, "Аня Серия"), (2, "Боря Сегодня"), (3, "Вася Пропуск"), (4, "Гоша Проверка")):
        await register(bot, uid, name)
    a, b, c, g = [(await get_user(sessions, uid)).id for uid in (1, 2, 3, 4)]
    zid = await quest(sessions, "Зарядка", QuestRepeat.daily, teacher.id)
    async with sessions() as s:  # серии создаются для ежедневных квестов
        await ach_service.ensure_defaults(s)

    await done(sessions, a, zid, *(day(d) for d in range(14, 19)))  # 14–18 октября: 5 дней
    await done(sessions, b, zid, day(18), day(19, 7))  # сегодня уже сделал
    await done(sessions, c, zid, day(17))  # вчера пропустил — серии нет
    await done(sessions, g, zid, day(18))
    await done(sessions, g, zid, day(19, 7), status=SubmissionStatus.pending)

    result = await collect(sessions)
    assert set(result) == {"Аня Серия"}
    text = reminders_service.text(result["Аня Серия"])
    assert "Не прервите серию" in text
    assert "🔥 «Зарядка» — 5 дней подряд, ещё 2 дня до «Зарядка: неделя подряд» 🔥" in text
    assert [q.id for q in result["Аня Серия"].quests] == [zid]


async def test_weekly_and_monthly(env):
    bot, client, sessions = env
    await make_teacher(bot, 7)
    teacher = await get_user(sessions, 7)
    await register(bot, 1, "Аня Неделя")
    a = (await get_user(sessions, 1)).id
    wid = await quest(sessions, "Отчёт", QuestRepeat.weekly, teacher.id)
    mid = await quest(sessions, "Проект", QuestRepeat.monthly, teacher.id)
    await done(sessions, a, wid, day(14))  # прошлая неделя
    await done(sessions, a, mid, day(5))  # этот месяц

    result = await collect(sessions)  # понедельник 19-го
    assert result["Аня Неделя"].lines == ["🔁 «Отчёт» снова доступен на этой неделе"]
    # во вторник о недельном не напоминаем
    assert await collect(sessions, MONDAY + timedelta(days=1)) == {}
    # 1 ноября — о месячном (выполнен в октябре)
    november = datetime(2026, 11, 1, 8, 0, tzinfo=MSK)
    assert (await collect(sessions, november))["Аня Неделя"].lines == [
        "🔁 «Проект» снова доступен в этом месяце"
    ]


async def test_closed_and_other_class_quests_skipped(env):
    bot, client, sessions = env
    await make_teacher(bot, 7)
    teacher = await get_user(sessions, 7)
    await register(bot, 1, "Аня Ученица")
    a = (await get_user(sessions, 1)).id
    other = await add_class(sessions, "5Б")
    foreign = await quest(sessions, "Чужой", QuestRepeat.daily, teacher.id, class_id=other)
    closed = await quest(sessions, "Закрытый", QuestRepeat.daily, teacher.id)
    async with sessions() as s:
        (await s.get(Quest, closed)).is_active = False
        await s.commit()
    await done(sessions, a, foreign, day(18))
    await done(sessions, a, closed, day(18))
    assert await collect(sessions) == {}


async def test_reminders_off(env):
    from app import keyboards as kb
    from tests.test_bot import buttons, cb

    bot, client, sessions = env
    await make_teacher(bot, 7)
    teacher = await get_user(sessions, 7)
    await register(bot, 1, "Аня Серия")
    a = (await get_user(sessions, 1)).id
    zid = await quest(sessions, "Зарядка", QuestRepeat.daily, teacher.id)
    await done(sessions, a, zid, day(18))
    assert "Аня Серия" in await collect(sessions)

    # кнопка под напоминанием
    payloads = [b["payload"] for b in buttons(kb.reminder_actions([]))]
    assert "rem:off" in payloads
    await bot.handle_update(cb(1, "rem:off"))
    assert "напоминания выключены" in client.answers[-1]["text"]
    assert await collect(sessions) == {}

    # в профиле — статус и кнопка включения
    await bot.handle_update(cb(1, "menu:profile"))
    answer = client.answers[-1]
    assert "🔕 Утренние напоминания выключены" in answer["text"]
    assert "rem:on" in [b["payload"] for b in buttons(answer["attachments"])]
    await bot.handle_update(cb(1, "rem:on"))
    assert "снова включены" in client.answers[-1]["text"]
    assert "Аня Серия" in await collect(sessions)

    # учителю переключатель не нужен
    await bot.handle_update(cb(7, "rem:off"))
    assert "только ученикам" in client.answers[-1]["notification"]
