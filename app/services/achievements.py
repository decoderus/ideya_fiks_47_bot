"""Достижения: автоматические (серии, квесты, монеты, покупки) и выдаваемые админом.

Стандартные автодостижения и серии для ежедневных квестов создаются сами
(ensure_defaults), их бонус админ может поменять. Бонус монетами начисляется
при получении и записывается в историю баланса.
"""
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Achievement,
    AchievementKind,
    CoinTransaction,
    Order,
    OrderStatus,
    Quest,
    QuestRepeat,
    Role,
    Submission,
    SubmissionStatus,
    User,
    UserAchievement,
)

MSK = timezone(timedelta(hours=3))
# Прогресс достижений считается только с этого момента: выполненное раньше
# в зачёт не идёт, задним числом ничего не выдаётся
START = datetime(2026, 10, 18, 22, 45, tzinfo=MSK)
TITLE_MAX = 64
DESCRIPTION_MAX = 300
EMOJI_MAX = 8
REWARD_MAX = 100_000
DEFAULT_EMOJI = "🏅"

# (вид, порог, эмодзи, название, описание, бонус)
DEFAULTS = [
    (AchievementKind.quests_count, 1, "🎯", "Первый шаг", "Выполнить первый квест", 20),
    (AchievementKind.quests_count, 10, "⭐", "Десятка", "Выполнить 10 квестов", 100),
    (AchievementKind.quests_count, 50, "🌟", "Полсотни", "Выполнить 50 квестов", 300),
    (AchievementKind.quests_count, 100, "💫", "Сотня", "Выполнить 100 квестов", 500),
    (AchievementKind.coins_earned, 1000, "🪙", "Первая тысяча", "Заработать 1 000 монет", 100),
    (AchievementKind.coins_earned, 5000, "💰", "Копилка", "Заработать 5 000 монет", 300),
    (AchievementKind.coins_earned, 10000, "💎", "Сокровищница", "Заработать 10 000 монет", 500),
    (AchievementKind.first_purchase, 1, "🛍", "Первая покупка", "Купить награду в магазине", 20),
]
# Серии для каждого ежедневного квеста: (дней подряд, эмодзи, название, бонус)
STREAKS = [(7, "🔥", "неделя подряд", 100), (30, "🏆", "месяц подряд", 500)]


class AchievementError(Exception):
    """Ошибка, текст которой можно показать пользователю."""


@dataclass
class Stats:
    quests_done: int = 0
    earned: int = 0
    purchases: int = 0
    # квест -> (лучшая серия, текущая серия) в днях
    streaks: dict[int, tuple[int, int]] = field(default_factory=dict)


# --- каталог ---


async def ensure_defaults(session: AsyncSession) -> None:
    """Создаёт недостающие стандартные автодостижения и серии ежедневных квестов."""
    rows = await session.execute(
        select(Achievement.kind, Achievement.threshold, Achievement.quest_id).where(
            Achievement.kind != AchievementKind.manual
        )
    )
    existing = {(kind, threshold, quest_id) for kind, threshold, quest_id in rows}
    added = False
    for kind, threshold, emoji, title, description, reward in DEFAULTS:
        if (kind, threshold, None) not in existing:
            session.add(
                Achievement(
                    kind=kind,
                    threshold=threshold,
                    emoji=emoji,
                    title=title,
                    description=description,
                    reward=reward,
                )
            )
            added = True
    daily = await session.scalars(select(Quest).where(Quest.repeat == QuestRepeat.daily))
    for quest in daily.unique():
        for days, emoji, suffix, reward in STREAKS:
            if (AchievementKind.streak, days, quest.id) not in existing:
                title, description = _streak_texts(quest, days)
                session.add(
                    Achievement(
                        kind=AchievementKind.streak,
                        threshold=days,
                        quest_id=quest.id,
                        emoji=emoji,
                        title=title,
                        description=description,
                        reward=reward,
                    )
                )
                added = True
    if added:
        await session.commit()


def _streak_texts(quest: Quest, days: int) -> tuple[str, str]:
    suffixes = {d: suffix for d, _, suffix, _ in STREAKS}
    suffix = suffixes.get(days, f"{days} дней подряд")
    return f"{quest.title}: {suffix}"[:TITLE_MAX], f"Выполнять «{quest.title}» {days} дней подряд"


async def rename_streaks(session: AsyncSession, quest: Quest) -> None:
    """После переименования квеста — обновить названия его серий (без commit)."""
    for a in await session.scalars(
        select(Achievement).where(
            Achievement.kind == AchievementKind.streak, Achievement.quest_id == quest.id
        )
    ):
        a.title, a.description = _streak_texts(quest, a.threshold or 0)


async def all_achievements(session: AsyncSession) -> list[Achievement]:
    await ensure_defaults(session)
    order = {
        AchievementKind.manual: 0,
        AchievementKind.streak: 1,
        AchievementKind.quests_count: 2,
        AchievementKind.coins_earned: 3,
        AchievementKind.first_purchase: 4,
    }
    items = list((await session.scalars(select(Achievement))).unique())
    return sorted(items, key=lambda a: (order[a.kind], a.quest_id or 0, a.threshold or 0, a.id))


async def holders_count(session: AsyncSession) -> dict[int, int]:
    rows = await session.execute(
        select(UserAchievement.achievement_id, func.count()).group_by(
            UserAchievement.achievement_id
        )
    )
    return {achievement_id: count for achievement_id, count in rows}


async def get(session: AsyncSession, achievement_id: int) -> Achievement:
    achievement = await session.get(Achievement, achievement_id)
    if achievement is None:
        raise AchievementError("Достижение не найдено.")
    return achievement


def parse_reward(text: str) -> int:
    try:
        reward = int(text.strip())
    except ValueError:
        raise AchievementError("Бонус должен быть целым числом, можно 0.")
    if not 0 <= reward <= REWARD_MAX:
        raise AchievementError(f"Бонус должен быть от 0 до {REWARD_MAX}.")
    return reward


async def create_manual(
    session: AsyncSession, admin: User, title: str, description: str, emoji: str, reward: int
) -> Achievement:
    if admin.role != Role.admin:
        raise AchievementError("Создавать достижения может только админ.")
    achievement = Achievement(
        kind=AchievementKind.manual,
        title=title[:TITLE_MAX],
        description=description[:DESCRIPTION_MAX],
        emoji=emoji or DEFAULT_EMOJI,
        reward=reward,
        author_id=admin.id,
    )
    session.add(achievement)
    await session.commit()
    return achievement


async def set_reward(session: AsyncSession, achievement_id: int, reward: int) -> Achievement:
    achievement = await get(session, achievement_id)
    achievement.reward = reward
    await session.commit()
    return achievement


# --- выдача ---


async def has(session: AsyncSession, achievement_id: int, student: User) -> bool:
    return (
        await session.scalar(
            select(UserAchievement.id).where(
                UserAchievement.achievement_id == achievement_id,
                UserAchievement.user_id == student.id,
            )
        )
    ) is not None


async def grant(
    session: AsyncSession, achievement: Achievement, student: User, by: User | None = None
) -> None:
    """Выдать достижение и бонус. by=None — автоматически."""
    if await has(session, achievement.id, student):
        raise AchievementError("У ученика уже есть это достижение.")
    session.add(
        UserAchievement(
            achievement_id=achievement.id,
            user_id=student.id,
            granted_by=by.id if by else None,
        )
    )
    if achievement.reward:
        student.balance += achievement.reward
        session.add(
            CoinTransaction(
                user_id=student.id,
                actor_id=by.id if by else student.id,
                amount=achievement.reward,
                reason=f"Достижение «{achievement.title}»"[:200],
            )
        )
    await session.commit()


async def revoke(
    session: AsyncSession, achievement: Achievement, student: User, admin: User
) -> int:
    """Отозвать достижение, выданное админом. Бонус списывается, но не ниже нуля."""
    if achievement.kind != AchievementKind.manual:
        raise AchievementError("Автодостижения не отзываются.")
    owned = await session.scalar(
        select(UserAchievement).where(
            UserAchievement.achievement_id == achievement.id,
            UserAchievement.user_id == student.id,
        )
    )
    if owned is None:
        raise AchievementError("У ученика нет этого достижения.")
    await session.delete(owned)
    taken = min(achievement.reward, student.balance)
    if taken:
        student.balance -= taken
        session.add(
            CoinTransaction(
                user_id=student.id,
                actor_id=admin.id,
                amount=-taken,
                reason=f"Отозвано достижение «{achievement.title}»"[:200],
            )
        )
    await session.commit()
    return taken


# --- условия автодостижений ---


def _day(moment: datetime) -> date:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(MSK).date()


def _streaks(days: set[date], today: date) -> tuple[int, int]:
    """(лучшая серия, текущая серия). Текущая не прерывается, если сегодня ещё не выполнено."""
    best = run = 0
    previous = None
    for day in sorted(days):
        run = run + 1 if previous and day - previous == timedelta(days=1) else 1
        best = max(best, run)
        previous = day
    current = 0
    day = today if today in days else today - timedelta(days=1)
    while day in days:
        current += 1
        day -= timedelta(days=1)
    return best, current


def _counts(moment: datetime | None) -> bool:
    """Идёт ли событие в зачёт достижений (не раньше START)."""
    if moment is None:
        return False
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment >= START


async def stats(session: AsyncSession, student: User) -> Stats:
    """Прогресс ученика с момента START."""
    result = Stats()
    days: dict[int, set[date]] = {}
    submissions = await session.scalars(
        select(Submission).where(
            Submission.user_id == student.id, Submission.status == SubmissionStatus.approved
        )
    )
    for s in submissions.unique():
        # Засчитан, когда проверен (авто-квест — сразу при выполнении)
        if not _counts(s.reviewed_at or s.created_at):
            continue
        result.quests_done += 1
        result.earned += s.earned
        if s.quest.repeat == QuestRepeat.daily and _counts(s.created_at):
            days.setdefault(s.quest_id, set()).add(_day(s.created_at))

    orders = await session.scalars(
        select(Order).where(Order.user_id == student.id, Order.status != OrderStatus.cancelled)
    )
    for o in orders.unique():
        if _counts(o.created_at):
            result.purchases += 1
            result.earned += max(o.prize_won or 0, 0)  # провал не уменьшает «заработано»

    transactions = await session.scalars(
        select(CoinTransaction).where(
            CoinTransaction.user_id == student.id, CoinTransaction.amount > 0
        )
    )
    for t in transactions:
        if _counts(t.created_at):
            result.earned += t.amount
    today = datetime.now(MSK).date()
    result.streaks = {quest_id: _streaks(d, today) for quest_id, d in days.items()}
    return result


def progress(achievement: Achievement, s: Stats) -> tuple[int, int]:
    """(сколько есть, сколько нужно) для автодостижения."""
    threshold = achievement.threshold or 1
    if achievement.kind == AchievementKind.quests_count:
        return s.quests_done, threshold
    if achievement.kind == AchievementKind.coins_earned:
        return s.earned, threshold
    if achievement.kind == AchievementKind.first_purchase:
        return s.purchases, 1
    if achievement.kind == AchievementKind.streak:
        return s.streaks.get(achievement.quest_id or 0, (0, 0))[1], threshold
    return 0, threshold


def _reached(achievement: Achievement, s: Stats) -> bool:
    if achievement.kind == AchievementKind.streak:
        best = s.streaks.get(achievement.quest_id or 0, (0, 0))[0]
        return best >= (achievement.threshold or 1)
    value, threshold = progress(achievement, s)
    return value >= threshold


async def check_auto(session: AsyncSession, student: User) -> list[Achievement]:
    """Выдать ученику все заслуженные автодостижения. Возвращает новые.

    Бонус одного достижения может открыть следующее (монеты), поэтому проверка повторяется.
    """
    if student.role != Role.student or student.full_name is None:
        return []
    await ensure_defaults(session)
    granted: list[Achievement] = []
    while True:
        owned = set(
            await session.scalars(
                select(UserAchievement.achievement_id).where(UserAchievement.user_id == student.id)
            )
        )
        s = await stats(session, student)
        new = [
            a
            for a in await session.scalars(
                select(Achievement).where(Achievement.kind != AchievementKind.manual)
            )
            if a.id not in owned and _reached(a, s)
        ]
        if not new:
            return granted
        for achievement in new:
            await grant(session, achievement, student)
            granted.append(achievement)


# --- витрина ученика ---


@dataclass
class Showcase:
    earned: list[tuple[Achievement, datetime]]
    locked: list[tuple[Achievement, int, int]]  # (достижение, есть, нужно)


async def showcase(session: AsyncSession, student: User) -> Showcase:
    """Полученные и ещё закрытые достижения ученика.

    Закрытые — только автоматические и только по доступным ученику квестам;
    невыданные достижения админа не показываются, чтобы сохранить сюрприз.
    """
    await ensure_defaults(session)
    owned = (
        await session.scalars(
            select(UserAchievement)
            .where(UserAchievement.user_id == student.id)
            .order_by(UserAchievement.granted_at.desc())
        )
    ).unique()
    earned = [(u.achievement, u.granted_at) for u in owned]
    owned_ids = {a.id for a, _ in earned}
    s = await stats(session, student)
    locked = []
    for a in await all_achievements(session):
        if a.id in owned_ids or a.kind == AchievementKind.manual:
            continue
        if a.kind == AchievementKind.streak:
            quest = a.quest
            # Квест закрыт или больше не ежедневный — серию уже не набрать
            if quest is None or not quest.is_active or quest.repeat != QuestRepeat.daily:
                continue
            if quest.class_id is not None and quest.class_id != student.class_id:
                continue
        value, threshold = progress(a, s)
        locked.append((a, min(value, threshold), threshold))
    return Showcase(earned, locked)


async def count_for(session: AsyncSession, student: User) -> int:
    return await session.scalar(
        select(func.count()).select_from(UserAchievement).where(UserAchievement.user_id == student.id)
    )
