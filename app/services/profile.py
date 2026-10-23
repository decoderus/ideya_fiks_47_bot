"""Профиль ученика: баланс, места в рейтинге, квесты, серии, достижения."""
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Quest, QuestRepeat, Submission, SubmissionStatus, User
from app.services import achievements as ach_service
from app.services import journal as journal_service
from app.services import rating as rating_service
from app.services import seasons as seasons_service

RECENT_ACHIEVEMENTS = 3


@dataclass
class Streak:
    title: str
    days: int  # текущая серия
    done_today: bool


@dataclass
class Profile:
    season_title: str | None = None
    season_points: int = 0
    school_place: int | None = None
    school_total: int = 0
    class_place: int | None = None
    class_total: int = 0
    quests_done: int = 0
    earned: int = 0
    pending: int = 0
    streaks: list[Streak] = field(default_factory=list)
    achievements: int = 0
    achievements_total: int = 0
    recent: list[tuple] = field(default_factory=list)  # (достижение, когда получено)


def _place(entries: list[rating_service.Entry], user_id: int) -> int | None:
    return next((e.place for e in entries if e.user_id == user_id), None)


async def _streaks(session: AsyncSession, student: User) -> list[Streak]:
    """Текущие серии по открытым ежедневным квестам (за всё время, не только для достижений)."""
    rows = await session.execute(
        select(Quest.id, Quest.title, Submission.created_at)
        .join(Submission, Submission.quest_id == Quest.id)
        .where(
            Submission.user_id == student.id,
            Submission.status == SubmissionStatus.approved,
            Quest.repeat == QuestRepeat.daily,
            Quest.is_active,
        )
    )
    days: dict[int, set[date]] = {}
    titles: dict[int, str] = {}
    for quest_id, title, created_at in rows:
        days.setdefault(quest_id, set()).add(ach_service._day(created_at))
        titles[quest_id] = title
    today = datetime.now(ach_service.MSK).date()
    result = []
    for quest_id, quest_days in days.items():
        _, current = ach_service._streaks(quest_days, today)
        if current:
            result.append(Streak(titles[quest_id], current, today in quest_days))
    return sorted(result, key=lambda s: -s.days)


async def build(session: AsyncSession, student: User) -> Profile:
    profile = Profile()
    season = await seasons_service.current(session)
    profile.season_title = season.season.title if season.season else None
    school = await rating_service.leaderboard(session)
    profile.school_place, profile.school_total = _place(school, student.id), len(school)
    profile.season_points = next((e.score for e in school if e.user_id == student.id), 0)
    if student.class_id is not None:
        in_class = await rating_service.leaderboard(session, student.class_id)
        profile.class_place, profile.class_total = _place(in_class, student.id), len(in_class)

    summary = await journal_service.summary(session, student)
    profile.quests_done, profile.earned = summary.quests_done, summary.earned
    profile.pending = await session.scalar(
        select(func.count())
        .select_from(Submission)
        .where(Submission.user_id == student.id, Submission.status == SubmissionStatus.pending)
    )
    profile.streaks = await _streaks(session, student)

    show = await ach_service.showcase(session, student)
    profile.achievements = len(show.earned)
    profile.achievements_total = len(show.earned) + len(show.locked)
    profile.recent = show.earned[:RECENT_ACHIEVEMENTS]
    return profile
