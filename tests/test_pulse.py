"""«Пульс класса»: активность, соревнование, неактивные, нужна помощь, серии, успехи."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from app.db.models import (
    Quest,
    QuestKind,
    QuestRepeat,
    Submission,
    SubmissionStatus,
    Success,
    User,
)
from app.services import pulse as pulse_service
from tests.test_bot import (  # noqa: F401  env — фикстура
    ADMIN,
    buttons,
    cb,
    class_id,
    env,
    get_user,
    make_teacher,
    msg,
    register,
)

MSK = timezone(timedelta(hours=3))
NOW = datetime(2026, 10, 28, 12, 0, tzinfo=MSK)  # среда; неделя с 26.10
LONG_AGO = NOW - timedelta(days=30)


async def quest(sessions, title, repeat=QuestRepeat.none, kind=QuestKind.photo, active=True) -> int:
    async with sessions() as s:
        author = await s.scalar(select(User).where(User.max_user_id == ADMIN))
        q = Quest(title=title, reward=50, kind=kind, repeat=repeat, author_id=author.id, is_active=active)
        s.add(q)
        await s.commit()
        return q.id


async def submit(sessions, uid, quest_id, status, at):
    async with sessions() as s:
        user = await s.scalar(select(User).where(User.max_user_id == uid))
        s.add(
            Submission(
                quest_id=quest_id, user_id=user.id, status=status, created_at=at, reviewed_at=at,
                reward=50 if status == SubmissionStatus.approved else None,
            )
        )
        await s.commit()


async def school(bot, sessions):
    """Учитель 7; ученики 5, 6, 11, 12 в тестовом классе, зарегистрированы месяц назад."""
    await make_teacher(bot, 7)
    for uid, name in ((5, "Иванов Иван"), (6, "Петрова Мария"), (11, "Сидоров Олег"), (12, "Кузнецова Ольга")):
        await register(bot, uid, name)
    async with sessions() as s:
        await s.execute(update(User).values(created_at=LONG_AGO))
        await s.commit()


async def test_pulse_numbers(env):
    bot, client, sessions = env
    await school(bot, sessions)
    task = await quest(sessions, "Порядок на столе")
    daily = await quest(sessions, "Зарядка", QuestRepeat.daily)
    # 5: серия зарядки 4 дня подряд (по сегодня) — активен на этой неделе
    for d in range(4):
        await submit(sessions, 5, daily, SubmissionStatus.approved, NOW - timedelta(days=d))
    # 6: ответ отклонён 3 дня назад и не пересдан; на прошлой неделе был активен
    await submit(sessions, 6, task, SubmissionStatus.approved, NOW - timedelta(days=5))
    await submit(sessions, 6, task, SubmissionStatus.rejected, NOW - timedelta(days=3))
    # 11: последний ответ 20 дней назад — неактивен; 12 — ни разу
    await submit(sessions, 11, task, SubmissionStatus.approved, NOW - timedelta(days=20))
    async with sessions() as s:
        student5 = await s.scalar(select(User).where(User.max_user_id == 5))
        teacher = await s.scalar(select(User).where(User.max_user_id == 7))
        s.add(Success(student_id=student5.id, author_id=teacher.id, author_role=teacher.role,
                      category="sport", text="Молодец", coins=0, created_at=NOW - timedelta(days=1)))
        await s.commit()

    async with sessions() as s:
        pulse = await pulse_service.class_pulse(s, await class_id(sessions), NOW.astimezone(timezone.utc))
    names = lambda items: sorted(x[0].full_name for x in items)  # noqa: E731
    assert len(pulse.students) == 4
    assert {u for u in pulse.active} == {student5.id}
    assert pulse.active_before == 2  # Петрова (23.10) и Иванов (серия захватывает 25.10)
    assert not pulse.eligible and pulse.need_active == 4
    assert names(pulse.inactive) == ["Кузнецова Ольга", "Сидоров Олег"]
    assert [(u.full_name, q) for u, q in pulse.need_help] == [("Петрова Мария", "Порядок на столе")]
    assert [(u.full_name, q, d) for u, q, d in pulse.streaks] == [("Иванов Иван", "Зарядка", 4)]
    assert pulse.successes_week == 1


async def test_resubmitted_is_not_help(env):
    bot, client, sessions = env
    await school(bot, sessions)
    task = await quest(sessions, "Идея для школы")
    closed = await quest(sessions, "Закрытый", active=False)
    await submit(sessions, 5, task, SubmissionStatus.rejected, NOW - timedelta(days=3))
    await submit(sessions, 5, task, SubmissionStatus.pending, NOW - timedelta(days=2))  # пересдал
    await submit(sessions, 6, closed, SubmissionStatus.rejected, NOW - timedelta(days=1))  # квест закрыт
    await submit(sessions, 11, task, SubmissionStatus.rejected, NOW - timedelta(days=20))  # давно
    async with sessions() as s:
        pulse = await pulse_service.class_pulse(s, await class_id(sessions), NOW.astimezone(timezone.utc))
    assert pulse.need_help == []


async def test_new_student_not_inactive(env):
    bot, client, sessions = env
    await make_teacher(bot, 7)
    await register(bot, 5)  # только что зарегистрировался
    async with sessions() as s:
        pulse = await pulse_service.class_pulse(s, await class_id(sessions))
    assert pulse.inactive == []


async def test_pulse_screens(env):
    bot, client, sessions = env
    await school(bot, sessions)
    task = await quest(sessions, "Порядок на столе")
    await submit(sessions, 6, task, SubmissionStatus.rejected, datetime.now(timezone.utc) - timedelta(days=1))
    cid = await class_id(sessions)

    await bot.handle_update(cb(7, f"jr:cls:{cid}:0"))
    answer = client.answers[-1]
    assert "пульс класса" in answer["text"] and "Активны: 0 из 4" in answer["text"]
    assert "до допуска не хватает активных — 5" in answer["text"]
    labels = {b["text"]: b["payload"] for b in buttons(answer["attachments"])}
    assert labels["😴 Неактивные · 3"] == f"jr:idle:{cid}"
    assert labels["⚠️ Нужна помощь · 1"] == f"jr:help:{cid}"
    assert labels["👥 Все ученики"] == f"jr:all:{cid}:0"
    assert labels["✍️ Отметить успех"] == f"jr:nc:{cid}:0"

    await bot.handle_update(cb(7, f"jr:help:{cid}"))
    help_buttons = buttons(client.answers[-1]["attachments"])
    petrova = await get_user(sessions, 6)
    assert (help_buttons[0]["text"], help_buttons[0]["payload"]) == (
        "Петрова Мария · «Порядок на столе»", f"jr:st:{petrova.id}:0"
    )
    assert help_buttons[-2]["payload"] == f"jr:cls:{cid}:0"  # « Пульс класса

    await bot.handle_update(cb(7, f"jr:idle:{cid}"))
    assert all("ещё ни разу" in b["text"] for b in buttons(client.answers[-1]["attachments"])[:3])

    # модератор видит пульс, но без «Отметить успех»
    await register(bot, 30, "Модеров Максим")
    await bot.handle_update(msg(ADMIN, "/role 30 moderator"))
    await bot.handle_update(cb(30, f"jr:cls:{cid}:0"))
    payloads = [b["payload"] for b in buttons(client.answers[-1]["attachments"])]
    assert f"jr:all:{cid}:0" in payloads and f"jr:nc:{cid}:0" not in payloads
