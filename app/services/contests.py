"""Соревнование классов: итоги недели (по понедельникам) и месяца (1-го числа).

Две номинации: больше всех монет за период и больше всех монет на одного ученика
класса (защищает небольшие классы). К участию допускаются классы, где за период
хотя бы MIN_ACTIVE учеников выполнили квест.

Заработок: засчитанные квесты (по времени засчитывания) и начисления монет —
от учителей и админа, бонусы за достижения и оповещения. Выигрыши в колесе
фортуны не считаются: это удача, а не активность.
"""
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Achievement,
    AchievementKind,
    ClassContestResult,
    CoinTransaction,
    ContestCriterion,
    ContestPeriod,
    QuestRepeat,
    Role,
    SchoolClass,
    User,
)
from app.services import achievements as ach_service
from app.services import quests as quests_service
from app.services import seasons as seasons_service

MSK = timezone(timedelta(hours=3))
MIN_ACTIVE = 5
PRIZE_PLACES = 3
# Монет каждому активному участнику класса-призёра: место -> сумма
REWARDS = {
    ContestPeriod.week: {1: 250, 2: 150, 3: 100},
    ContestPeriod.month: {1: 500, 2: 300, 3: 200},
}
BADGES = {ContestPeriod.week: "🏆 Класс недели", ContestPeriod.month: "👑 Класс месяца"}
TEAM_ACHIEVEMENT = ("🤝", "Командная победа", "Твой класс занял 1-е место в соревновании классов")


@dataclass
class ClassScore:
    class_id: int
    name: str
    total: int = 0
    students: int = 0
    active: int = 0
    active_ids: set[int] = field(default_factory=set)  # кто выполнил квест за период

    @property
    def average(self) -> int:
        return round(self.total / self.students) if self.students else 0

    @property
    def eligible(self) -> bool:
        return self.active >= MIN_ACTIVE


def current_bounds(period: ContestPeriod, now: datetime) -> tuple[datetime, datetime]:
    """Текущий период: [начало, now)."""
    repeat = QuestRepeat.weekly if period == ContestPeriod.week else QuestRepeat.monthly
    return quests_service.period_start(repeat, now), now


def previous_bounds(period: ContestPeriod, now: datetime) -> tuple[datetime, datetime]:
    """Прошлый (завершённый) период: [начало, конец)."""
    end, _ = current_bounds(period, now)
    if period == ContestPeriod.week:
        return end - timedelta(days=7), end
    local = end.astimezone(MSK)
    start = (local - timedelta(days=1)).replace(day=1)
    return start.astimezone(timezone.utc), end


def local_day(moment: datetime) -> date:
    return moment.astimezone(MSK).date()


async def scores(session: AsyncSession, start: datetime, end: datetime) -> list[ClassScore]:
    """Результаты всех классов за [start, end)."""
    classes = {c.id: ClassScore(c.id, c.name) for c in await session.scalars(select(SchoolClass))}
    students = {
        u.id: u.class_id
        for u in (
            await session.scalars(
                select(User).where(
                    User.role == Role.student,
                    User.full_name.is_not(None),
                    User.class_id.is_not(None),
                )
            )
        ).unique()
    }
    for class_id in students.values():
        if class_id in classes:
            classes[class_id].students += 1

    points, active_users = await seasons_service.earnings(session, start, end)
    active: dict[int, set[int]] = {}
    for user_id, class_id in students.items():
        score = classes.get(class_id)
        if score is None:
            continue
        score.total += points.get(user_id, 0)
        if user_id in active_users:
            active.setdefault(class_id, set()).add(user_id)

    for class_id, members in active.items():
        classes[class_id].active = len(members)
        classes[class_id].active_ids = members
    return list(classes.values())


def ranking(items: list[ClassScore], criterion: ContestCriterion) -> list[ClassScore]:
    """Допущенные классы по убыванию; при равенстве — больше активных, затем по названию."""
    key = (lambda c: c.total) if criterion == ContestCriterion.total else (lambda c: c.average)
    eligible = [c for c in items if c.eligible and key(c) > 0]
    return sorted(eligible, key=lambda c: (-key(c), -c.active, c.name))


@dataclass
class Summary:
    period: ContestPeriod
    start: date
    end: date  # последний день периода
    winners: dict[ContestCriterion, list[ClassScore]]


async def summarize(
    session: AsyncSession, period: ContestPeriod, now: datetime
) -> Summary | None:
    """Подвести итоги прошлого периода и сохранить призёров. None — уже подведены."""
    start, end = previous_bounds(period, now)
    first_day = local_day(start)
    done = await session.scalar(
        select(ClassContestResult.id).where(
            ClassContestResult.period == period, ClassContestResult.period_start == first_day
        )
    )
    if done is not None:
        return None
    items = await scores(session, start, end)
    winners = {c: ranking(items, c)[:PRIZE_PLACES] for c in ContestCriterion}
    for criterion, top in winners.items():
        for place, score in enumerate(top, start=1):
            session.add(
                ClassContestResult(
                    period=period,
                    period_start=first_day,
                    criterion=criterion,
                    place=place,
                    class_id=score.class_id,
                    class_name=score.name,
                    value=score.total if criterion == ContestCriterion.total else score.average,
                    active=score.active,
                    students=score.students,
                )
            )
    if not any(winners.values()):
        # Отметка «итоги подведены» без призёров, чтобы не подводить их повторно
        session.add(
            ClassContestResult(
                period=period,
                period_start=first_day,
                criterion=ContestCriterion.total,
                place=0,
                class_name="",
                value=0,
                active=0,
                students=0,
            )
        )
    await session.commit()
    return Summary(period, first_day, local_day(end - timedelta(seconds=1)), winners)


async def last_results(
    session: AsyncSession, period: ContestPeriod
) -> tuple[date | None, dict[ContestCriterion, list[ClassContestResult]]]:
    latest = await session.scalar(
        select(ClassContestResult.period_start)
        .where(ClassContestResult.period == period)
        .order_by(ClassContestResult.period_start.desc())
        .limit(1)
    )
    if latest is None:
        return None, {}
    rows = await session.scalars(
        select(ClassContestResult)
        .where(
            ClassContestResult.period == period,
            ClassContestResult.period_start == latest,
            ClassContestResult.place > 0,
        )
        .order_by(ClassContestResult.place)
    )
    result: dict[ContestCriterion, list[ClassContestResult]] = {}
    for r in rows:
        result.setdefault(r.criterion, []).append(r)
    return latest, result


# --- награды ---


@dataclass
class Prize:
    student: User
    class_name: str
    place: int
    amount: int
    achievement: Achievement | None  # «Командная победа», если получена впервые


async def reward(session: AsyncSession, summary: Summary) -> list[Prize]:
    """Наградить активных участников классов-призёров.

    Класс получает одну награду за период — за лучшее место из двух номинаций.
    """
    best: dict[int, tuple[int, ClassScore]] = {}
    for top in summary.winners.values():
        for place, score in enumerate(top, start=1):
            if score.class_id not in best or place < best[score.class_id][0]:
                best[score.class_id] = (place, score)
    if not best:
        return []
    team = await _team_achievement(session)
    title = "недели" if summary.period == ContestPeriod.week else "месяца"
    prizes: list[Prize] = []
    for place, score in sorted(best.values(), key=lambda item: item[0]):
        amount = REWARDS[summary.period][place]
        for user_id in sorted(score.active_ids):
            student = await session.get(User, user_id)
            if student is None or student.role != Role.student:
                continue
            student.balance += amount
            session.add(
                CoinTransaction(
                    user_id=student.id,
                    actor_id=student.id,
                    amount=amount,
                    reason=f"{seasons_service.CONTEST_REWARD_REASON}: {place} место {title}",
                )
            )
            await session.commit()
            new = None
            if place == 1 and not await ach_service.has(session, team.id, student):
                await ach_service.grant(session, team, student)
                new = team
            prizes.append(Prize(student, score.name, place, amount, new))
    return prizes


async def _team_achievement(session: AsyncSession) -> Achievement:
    emoji, title, description = TEAM_ACHIEVEMENT
    achievement = await session.scalar(
        select(Achievement).where(
            Achievement.kind == AchievementKind.manual, Achievement.title == title
        )
    )
    if achievement is None:
        achievement = Achievement(
            kind=AchievementKind.manual, title=title, description=description, emoji=emoji, reward=0
        )
        session.add(achievement)
        await session.commit()
    return achievement


async def badges(session: AsyncSession) -> dict[int, list[str]]:
    """Значки классов-победителей последних итогов: класс -> [«🏆 Класс недели», ...]."""
    result: dict[int, list[str]] = {}
    for period in ContestPeriod:
        _, results = await last_results(session, period)
        winners = {r.class_id for rows in results.values() for r in rows if r.place == 1}
        for class_id in winners:
            if class_id is not None:
                result.setdefault(class_id, []).append(BADGES[period])
    return result
