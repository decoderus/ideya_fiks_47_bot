"""Утренние напоминания: не прервать серию повторяемого квеста.

Ежедневный квест — если выполнен вчера и не выполнен сегодня.
Еженедельный — в понедельник, если выполнен на прошлой неделе.
Ежемесячный — 1-го числа, если выполнен в прошлом месяце.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Achievement,
    AchievementKind,
    Quest,
    QuestRepeat,
    Role,
    Submission,
    SubmissionStatus,
    User,
    UserAchievement,
)
from app.services import achievements as ach_service
from app.services import quests as quests_service
from app.ui import days_word as _days

MSK = timezone(timedelta(hours=3))
# Подсказать про достижение-серию, если до него осталось не больше стольких дней
HINT_DAYS = 3


@dataclass
class Reminder:
    student: User
    lines: list[str] = field(default_factory=list)
    quests: list[Quest] = field(default_factory=list)


def _utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _previous_start(repeat: QuestRepeat, current: datetime) -> datetime:
    """Начало предыдущего периода (current — начало текущего, в UTC)."""
    if repeat == QuestRepeat.daily:
        return current - timedelta(days=1)
    if repeat == QuestRepeat.weekly:
        return current - timedelta(days=7)
    # Месяц: 1-е число предыдущего месяца по Москве
    local = current.astimezone(MSK)
    previous = (local - timedelta(days=1)).replace(day=1)
    return previous.astimezone(timezone.utc)


def is_due(repeat: QuestRepeat, now: datetime) -> bool:
    """Напоминать ли сегодня: ежедневные — всегда, недельные — в пн, месячные — 1-го."""
    local = now.astimezone(MSK)
    if repeat == QuestRepeat.daily:
        return True
    if repeat == QuestRepeat.weekly:
        return local.weekday() == 0
    return local.day == 1


async def collect(session: AsyncSession, now: datetime | None = None) -> list[Reminder]:
    now = now or datetime.now(timezone.utc)
    quests = [
        q
        for q in (
            await session.scalars(
                select(Quest).where(Quest.is_active, Quest.repeat != QuestRepeat.none)
            )
        ).unique()
        if is_due(q.repeat, now)
    ]
    if not quests:
        return []
    by_id = {q.id: q for q in quests}

    # Все ответы по этим квестам: approved и pending
    rows = await session.execute(
        select(Submission.user_id, Submission.quest_id, Submission.status, Submission.created_at)
        .where(
            Submission.quest_id.in_(list(by_id)),
            or_(
                Submission.status == SubmissionStatus.approved,
                Submission.status == SubmissionStatus.pending,
            ),
        )
    )
    history: dict[tuple[int, int], list[tuple[SubmissionStatus, datetime]]] = {}
    for user_id, quest_id, status, created_at in rows:
        history.setdefault((user_id, quest_id), []).append((status, _utc(created_at)))
    if not history:
        return []

    students = {
        u.id: u
        for u in (
            await session.scalars(
                select(User).where(
                    User.id.in_({user_id for user_id, _ in history}),
                    User.role == Role.student,
                    User.full_name.is_not(None),
                    User.reminders_off.is_(False),
                )
            )
        ).unique()
    }
    owned = {
        (user_id, achievement_id)
        for user_id, achievement_id in await session.execute(
            select(UserAchievement.user_id, UserAchievement.achievement_id)
        )
    }
    streak_achievements = list(
        await session.scalars(
            select(Achievement).where(Achievement.kind == AchievementKind.streak)
        )
    )
    today = now.astimezone(MSK).date()

    reminders: dict[int, Reminder] = {}
    for (user_id, quest_id), items in history.items():
        student, quest = students.get(user_id), by_id[quest_id]
        if student is None:
            continue
        if quest.class_id is not None and quest.class_id != student.class_id:
            continue
        start = quests_service.period_start(quest.repeat, now)
        previous = _previous_start(quest.repeat, start)
        # В текущем периоде уже выполнен или ждёт проверки — напоминать не о чем
        if any(t >= start for _, t in items) or any(s == SubmissionStatus.pending for s, _ in items):
            continue
        if not any(previous <= t < start for s, t in items if s == SubmissionStatus.approved):
            continue

        reminder = reminders.setdefault(user_id, Reminder(student))
        reminder.quests.append(quest)
        if quest.repeat == QuestRepeat.daily:
            days = {ach_service._day(t) for s, t in items if s == SubmissionStatus.approved}
            _, current = ach_service._streaks(days, today)
            line = f"🔥 «{quest.title}» — {current} {_days(current)} подряд"
            # Ближайшее неполученное достижение-серия (прогресс — с запуска достижений)
            since = {
                ach_service._day(t)
                for s, t in items
                if s == SubmissionStatus.approved and t >= ach_service.START
            }
            _, counted = ach_service._streaks(since, today)
            targets = sorted(
                (a for a in streak_achievements
                 if a.quest_id == quest_id and (user_id, a.id) not in owned),
                key=lambda a: a.threshold or 0,
            )
            if targets and 0 < (targets[0].threshold or 0) - counted <= HINT_DAYS:
                left = targets[0].threshold - counted
                line += f", ещё {left} {_days(left)} до «{targets[0].title}» {targets[0].emoji}"
            reminder.lines.append(line)
        elif quest.repeat == QuestRepeat.weekly:
            reminder.lines.append(f"🔁 «{quest.title}» снова доступен на этой неделе")
        else:
            reminder.lines.append(f"🔁 «{quest.title}» снова доступен в этом месяце")
    return list(reminders.values())


def text(reminder: Reminder) -> str:
    return "☀️ Доброе утро! Не прервите серию:\n\n" + "\n".join(reminder.lines)
