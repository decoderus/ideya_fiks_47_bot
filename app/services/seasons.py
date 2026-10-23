"""Сезоны рейтинга (четверти).

Монеты на балансе не сгорают: рейтинг считает очки сезона — монеты, заработанные
с начала сезона (засчитанные квесты и начисления; выигрыши колеса фортуны не идут).
Сезон длится до начала следующего. Даты задаёт админ; если сезонов нет, создаются
типичные четверти текущего учебного года.
"""
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Achievement,
    AchievementKind,
    CoinTransaction,
    Role,
    SchoolClass,
    Season,
    SeasonResult,
    Submission,
    SubmissionStatus,
    User,
)
from app.services import achievements as ach_service

MSK = timezone(timedelta(hours=3))
TITLE_MAX = 64
# Начисления с такой причиной — награды соревнования классов: на балансе они есть,
# но в очки сезона и соревнования не идут, чтобы победитель не получал фору
CONTEST_REWARD_REASON = "🏁 Соревнование классов"
TOP_SCHOOL = 10
# (номер четверти, месяц, день начала) — типичный график, админ может поменять
# Сезон начинается в первый день каникул: итоги четверти приходят, когда она закончилась,
# а очки за каникулы идут в следующий сезон. Даты — по графику 2026/27, админ может поменять.
DEFAULT_QUARTERS = [("I", 9, 1), ("II", 10, 26), ("III", 12, 31), ("IV", 3, 27)]
SUMMER_START = (5, 27)  # летние каникулы — отдельный сезон до 1 сентября
# Достижения призёрам школы: место -> (эмодзи, название)
PRIZES = {1: ("🥇", "Чемпион"), 2: ("🥈", "Серебро"), 3: ("🥉", "Бронза")}


class SeasonError(Exception):
    """Ошибка, текст которой можно показать пользователю."""


def today() -> date:
    return datetime.now(MSK).date()


def moment(day: date) -> datetime:
    """Начало дня по Москве, в UTC."""
    return datetime.combine(day, time(), MSK).astimezone(timezone.utc)


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


# --- очки ---


async def earnings(
    session: AsyncSession, start: datetime | None, end: datetime | None
) -> tuple[dict[int, int], set[int]]:
    """Заработок учеников за [start, end) и кто выполнил хотя бы один квест.

    None в границе — без ограничения с этой стороны.
    """

    def inside(value: datetime | None) -> bool:
        if value is None:
            return False
        value = _utc(value)
        return (start is None or value >= start) and (end is None or value < end)

    points: dict[int, int] = {}
    active: set[int] = set()
    submissions = await session.scalars(
        select(Submission).where(Submission.status == SubmissionStatus.approved)
    )
    for s in submissions.unique():
        if inside(s.reviewed_at or s.created_at):
            points[s.user_id] = points.get(s.user_id, 0) + s.earned
            active.add(s.user_id)
    transactions = await session.scalars(select(CoinTransaction).where(CoinTransaction.amount > 0))
    for t in transactions.unique():
        if inside(t.created_at) and not t.reason.startswith(CONTEST_REWARD_REASON):
            points[t.user_id] = points.get(t.user_id, 0) + t.amount
    return points, active


# --- сезоны ---


def _school_year(day: date) -> int:
    return day.year if day.month >= 8 else day.year - 1


def default_seasons(day: date) -> list[tuple[str, date]]:
    """Четверти учебного года, в который попадает day, лето и I четверть следующего."""
    year = _school_year(day)
    label = f"{year}/{(year + 1) % 100:02d}"
    result = []
    for number, month, d in DEFAULT_QUARTERS:
        result.append((f"{number} четверть {label}", date(year if month >= 8 else year + 1, month, d)))
    result.append((f"Летний сезон {year + 1}", date(year + 1, *SUMMER_START)))
    next_label = f"{year + 1}/{(year + 2) % 100:02d}"
    result.append((f"I четверть {next_label}", date(year + 1, 9, 1)))
    return result


async def all_seasons(session: AsyncSession) -> list[Season]:
    """Сезоны по дате начала; если их нет — создаёт типичные четверти.

    Уже закончившиеся созданные сезоны сразу отмечаются закрытыми: итоги за прошлое
    задним числом не подводятся.
    """
    seasons = list(await session.scalars(select(Season).order_by(Season.start_date)))
    if seasons:
        return seasons
    now, day = datetime.now(timezone.utc), today()
    defaults = default_seasons(day)
    for i, (title, start) in enumerate(defaults):
        ended = i + 1 < len(defaults) and defaults[i + 1][1] <= day
        session.add(Season(title=title, start_date=start, closed_at=now if ended else None))
    await session.commit()
    return list(await session.scalars(select(Season).order_by(Season.start_date)))


@dataclass
class Bounds:
    season: Season | None
    start: datetime | None
    end: datetime | None  # None — сезон ещё идёт
    next_start: date | None


def bounds(seasons: list[Season], season: Season) -> Bounds:
    following = next((s for s in seasons if s.start_date > season.start_date), None)
    next_start = following.start_date if following else None
    return Bounds(season, moment(season.start_date), moment(next_start) if next_start else None, next_start)


async def current(session: AsyncSession) -> Bounds:
    """Идущий сезон; если ни один ещё не начался — рейтинг за всё время."""
    seasons = await all_seasons(session)
    day = today()
    started = [s for s in seasons if s.start_date <= day]
    if not started:
        following = seasons[0].start_date if seasons else None
        return Bounds(None, None, None, following)
    return bounds(seasons, started[-1])


# --- управление (админ) ---


def parse_date(text: str) -> date:
    match = re.fullmatch(r"\s*(\d{1,2})\.(\d{1,2})\.(\d{4})\s*", text)
    if not match:
        raise SeasonError("Напишите дату в виде ДД.ММ.ГГГГ, например 03.11.2026.")
    d, m, y = map(int, match.groups())
    try:
        return date(y, m, d)
    except ValueError:
        raise SeasonError("Такой даты нет.")


def _check_title(title: str) -> str:
    title = title.strip()
    if not title or len(title) > TITLE_MAX:
        raise SeasonError(f"Название: от 1 до {TITLE_MAX} символов.")
    return title


async def _check_date(session: AsyncSession, start: date, season_id: int | None = None) -> None:
    if start <= today():
        raise SeasonError("Дата начала должна быть в будущем: начавшийся сезон не переносят.")
    clash = await session.scalar(
        select(Season).where(Season.start_date == start, Season.id != (season_id or 0))
    )
    if clash:
        raise SeasonError(f"С этой даты уже начинается «{clash.title}».")


async def get(session: AsyncSession, season_id: int) -> Season:
    season = await session.get(Season, season_id)
    if season is None:
        raise SeasonError("Сезон не найден.")
    return season


async def add(session: AsyncSession, title: str, start: date) -> Season:
    title = _check_title(title)
    await _check_date(session, start)
    season = Season(title=title, start_date=start)
    session.add(season)
    await session.commit()
    return season


async def rename(session: AsyncSession, season_id: int, title: str) -> Season:
    season = await get(session, season_id)
    season.title = _check_title(title)
    await session.commit()
    return season


async def move(session: AsyncSession, season_id: int, start: date) -> Season:
    season = await get(session, season_id)
    if season.start_date <= today():
        raise SeasonError("Сезон уже начался, дату его начала менять нельзя.")
    await _check_date(session, start, season.id)
    season.start_date = start
    await session.commit()
    return season


async def delete(session: AsyncSession, season_id: int) -> str:
    season = await get(session, season_id)
    if season.start_date <= today():
        raise SeasonError("Начавшийся сезон удалить нельзя.")
    title = season.title
    await session.delete(season)
    await session.commit()
    return title


# --- итоги ---


@dataclass
class Standing:
    user_id: int
    name: str
    class_id: int | None
    points: int
    place: int


def rank(students: list[User], points: dict[int, int]) -> list[Standing]:
    """По убыванию очков; равные очки — одно место (1, 2, 2, 4)."""
    rows = sorted(students, key=lambda u: (-points.get(u.id, 0), u.full_name or ""))
    result: list[Standing] = []
    for i, u in enumerate(rows):
        score = points.get(u.id, 0)
        place = result[-1].place if result and result[-1].points == score else i + 1
        result.append(Standing(u.id, u.full_name or "", u.class_id, score, place))
    return result


async def students(session: AsyncSession, class_id: int | None = None) -> list[User]:
    query = select(User).where(User.role == Role.student, User.full_name.is_not(None))
    if class_id is not None:
        query = query.where(User.class_id == class_id)
    return list((await session.scalars(query)).unique())


@dataclass
class Closed:
    season: Season
    school: list[Standing]  # топ школы
    by_class: list[tuple[str, Standing]]  # лучший в каждом классе
    prizes: list[tuple[User, Achievement]]


async def close_due(session: AsyncSession) -> list[Closed]:
    """Подвести итоги всех закончившихся и ещё не закрытых сезонов."""
    seasons = await all_seasons(session)
    day = today()
    closed: list[Closed] = []
    for season in seasons:
        b = bounds(seasons, season)
        if season.closed_at is not None or b.next_start is None or b.next_start > day:
            continue
        closed.append(await _close(session, season, b))
    return closed


async def _close(session: AsyncSession, season: Season, b: Bounds) -> Closed:
    """Итоги сезона — по балансу на момент подведения (как в рейтинге)."""
    everyone = await students(session)
    points = {u.id: u.balance for u in everyone}
    school = [s for s in rank(everyone, points) if s.points > 0][:TOP_SCHOOL]
    for s in school:
        session.add(
            SeasonResult(season_id=season.id, place=s.place, user_id=s.user_id,
                         full_name=s.name, points=s.points)
        )
    by_class: list[tuple[str, Standing]] = []
    classes = {c.id: c.name for c in await session.scalars(select(SchoolClass))}
    for class_id, name in sorted(classes.items(), key=lambda item: item[1]):
        members = [u for u in everyone if u.class_id == class_id]
        top = [s for s in rank(members, points) if s.points > 0 and s.place == 1]
        for s in top:
            by_class.append((name, s))
            session.add(
                SeasonResult(season_id=season.id, class_id=class_id, class_name=name, place=1,
                             user_id=s.user_id, full_name=s.name, points=s.points)
            )
    season.closed_at = datetime.now(timezone.utc)
    await session.commit()

    prizes: list[tuple[User, Achievement]] = []
    users = {u.id: u for u in everyone}
    for s in school:
        if s.place not in PRIZES:
            continue
        emoji, word = PRIZES[s.place]
        achievement = await session.scalar(
            select(Achievement).where(
                Achievement.kind == AchievementKind.manual,
                Achievement.title == f"{word}: {season.title}",
            )
        )
        if achievement is None:
            achievement = Achievement(
                kind=AchievementKind.manual,
                title=f"{word}: {season.title}"[: ach_service.TITLE_MAX],
                description=f"{s.place}-е место в рейтинге школы за {season.title}",
                emoji=emoji,
                reward=0,
            )
            session.add(achievement)
            await session.commit()
        await ach_service.grant(session, achievement, users[s.user_id])
        prizes.append((users[s.user_id], achievement))
    return Closed(season, school, by_class, prizes)


async def archive(session: AsyncSession) -> list[Season]:
    return [s for s in await all_seasons(session) if s.closed_at is not None]


async def results(session: AsyncSession, season_id: int) -> list[SeasonResult]:
    return list(
        await session.scalars(
            select(SeasonResult)
            .where(SeasonResult.season_id == season_id)
            .order_by(SeasonResult.class_id.is_not(None), SeasonResult.class_name, SeasonResult.place)
        )
    )
