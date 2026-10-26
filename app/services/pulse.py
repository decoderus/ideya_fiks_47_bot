"""«Пульс класса»: первый экран класса в журнале успехов — где учителю действовать.

Активен — тот же смысл, что в соревновании классов: выполнил квест за неделю.
Неактивен — 2+ недели без отправленных ответов (и зарегистрирован не меньше 2 недель назад).
Нужна помощь — последний ответ на открытый квест отклонён и не пересдан (за последние 2 недели).
"""
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    ContestCriterion,
    ContestPeriod,
    QuestRepeat,
    SchoolClass,
    Submission,
    SubmissionStatus,
    Success,
    User,
    UserAchievement,
)
from app.services import achievements as ach_service
from app.services import classes as classes_service
from app.services import contests as contests_service

INACTIVE = timedelta(days=14)
HELP_WINDOW = timedelta(days=14)
MIN_STREAK = 3  # серии короче не показываем
STREAKS_SHOWN = 3


def _utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


@dataclass
class Pulse:
    school_class: SchoolClass
    students: list[User]
    week_start: date
    active: set[int] = field(default_factory=set)
    active_before: int = 0
    eligible: bool = False
    need_active: int = 0  # сколько активных не хватает до допуска
    place_total: int | None = None  # место по сумме монет среди допущенных
    place_average: int | None = None
    inactive: list[tuple[User, datetime | None]] = field(default_factory=list)  # (ученик, последняя активность)
    need_help: list[tuple[User, str]] = field(default_factory=list)  # (ученик, квест)
    streaks: list[tuple[User, str, int]] = field(default_factory=list)  # (ученик, квест, дней)
    new_achievements: int = 0
    successes_week: int = 0


def _place(items, class_id: int, criterion: ContestCriterion) -> int | None:
    for i, score in enumerate(contests_service.ranking(items, criterion), 1):
        if score.class_id == class_id:
            return i
    return None


async def class_pulse(session: AsyncSession, class_id: int, now: datetime | None = None) -> Pulse | None:
    school_class = await session.get(SchoolClass, class_id)
    if school_class is None:
        return None
    now = now or datetime.now(timezone.utc)
    students = await classes_service.students(session, class_id)
    ids = [s.id for s in students]
    week_start, _ = contests_service.current_bounds(ContestPeriod.week, now)
    pulse = Pulse(school_class, students, contests_service.local_day(week_start))

    # Активность и соревнование — как в соревновании классов
    current = await contests_service.scores(session, week_start, now)
    mine = next((c for c in current if c.class_id == class_id), None)
    if mine:
        pulse.active = mine.active_ids
        pulse.eligible = mine.eligible
        pulse.need_active = max(contests_service.MIN_ACTIVE - mine.active, 0)
        pulse.place_total = _place(current, class_id, ContestCriterion.total)
        pulse.place_average = _place(current, class_id, ContestCriterion.average)
    prev_start, prev_end = contests_service.previous_bounds(ContestPeriod.week, now)
    before = await contests_service.scores(session, prev_start, prev_end)
    pulse.active_before = next((c.active for c in before if c.class_id == class_id), 0)
    if not ids:
        return pulse

    submissions = list(
        (
            await session.scalars(
                select(Submission).where(Submission.user_id.in_(ids)).order_by(Submission.created_at)
            )
        ).unique()
    )
    by_id = {s.id: s for s in students}

    # Неактивные: последняя отправка (или регистрация) старше 2 недель
    last: dict[int, datetime] = {}
    for sub in submissions:
        last[sub.user_id] = _utc(sub.created_at)
    for s in students:
        seen = last.get(s.id)
        if max(seen or _utc(s.created_at), _utc(s.created_at)) < now - INACTIVE:
            pulse.inactive.append((s, seen))
    pulse.inactive.sort(key=lambda item: item[1] or datetime.min.replace(tzinfo=timezone.utc))

    # Нужна помощь: последний ответ на открытый квест отклонён и не пересдан
    latest: dict[tuple[int, int], Submission] = {}
    for sub in submissions:
        latest[(sub.user_id, sub.quest_id)] = sub
    helped: set[int] = set()
    for (user_id, _), sub in sorted(latest.items(), key=lambda kv: kv[1].created_at, reverse=True):
        if (
            sub.status == SubmissionStatus.rejected
            and sub.quest.is_active
            and _utc(sub.reviewed_at or sub.created_at) >= now - HELP_WINDOW
            and user_id not in helped
        ):
            helped.add(user_id)
            pulse.need_help.append((by_id[user_id], sub.quest.title))

    # Серии ежедневных квестов — как для достижений и утренних напоминаний
    days: dict[tuple[int, int], set[date]] = {}
    titles: dict[int, str] = {}
    for sub in submissions:
        if sub.status == SubmissionStatus.approved and sub.quest.repeat == QuestRepeat.daily:
            days.setdefault((sub.user_id, sub.quest_id), set()).add(ach_service._day(sub.created_at))
            titles[sub.quest_id] = sub.quest.title
    today = contests_service.local_day(now)
    found = []
    for (user_id, quest_id), d in days.items():
        current_streak = ach_service._streaks(d, today)[1]
        if current_streak >= MIN_STREAK:
            found.append((by_id[user_id], titles[quest_id], current_streak))
    pulse.streaks = sorted(found, key=lambda x: -x[2])[:STREAKS_SHOWN]

    pulse.new_achievements = await session.scalar(
        select(func.count()).select_from(UserAchievement).where(
            UserAchievement.user_id.in_(ids), UserAchievement.granted_at >= week_start
        )
    ) or 0
    pulse.successes_week = await session.scalar(
        select(func.count()).select_from(Success).where(
            Success.student_id.in_(ids), Success.created_at >= week_start
        )
    ) or 0
    return pulse
