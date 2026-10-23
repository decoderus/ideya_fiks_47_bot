"""Журнал успехов: карточка ученика с итогами и историей (для учителя и админа)."""
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    CoinTransaction,
    Order,
    OrderStatus,
    RewardKind,
    Role,
    Submission,
    SubmissionStatus,
    User,
)

HISTORY_LIMIT = 10


def _utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


class JournalError(Exception):
    """Ошибка, текст которой можно показать пользователю."""


@dataclass
class Event:
    at: datetime
    text: str
    delta: int = 0  # изменение баланса
    after: int | None = None  # баланс после события (в истории баланса)


@dataclass
class Summary:
    quests_done: int = 0
    earned: int = 0  # квесты + ручные начисления + выигрыши в колесе фортуны
    purchases: int = 0  # покупки, кроме отменённых
    events: list[Event] = field(default_factory=list)  # новые сверху


def can_view_class(user: User, class_id: int) -> bool:
    if user.role in (Role.admin, Role.moderator):
        return True
    return user.role == Role.teacher and class_id in user.teacher_class_ids


async def get_student(session: AsyncSession, viewer: User, user_id: int) -> User:
    student = await session.get(User, user_id)
    if (
        student is None
        or student.role != Role.student
        or student.full_name is None
        or student.class_id is None
        or not can_view_class(viewer, student.class_id)
    ):
        raise JournalError("Ученик не найден.")
    return student


async def quests_done(session: AsyncSession, user_ids: list[int]) -> dict[int, int]:
    """Сколько квестов засчитано каждому ученику."""
    if not user_ids:
        return {}
    rows = await session.execute(
        select(Submission.user_id, func.count())
        .where(Submission.user_id.in_(user_ids), Submission.status == SubmissionStatus.approved)
        .group_by(Submission.user_id)
    )
    return {user_id: count for user_id, count in rows}


async def summary(session: AsyncSession, student: User) -> Summary:
    result, events = await _collect(session, student)
    result.events = events[:HISTORY_LIMIT]
    return result


async def balance_history(session: AsyncSession, student: User) -> list[Event]:
    """Все изменения баланса, новые сверху, с балансом после каждого.

    Баланс «после» считается назад от текущего, поэтому всегда сходится с балансом сейчас.
    """
    _, events = await _collect(session, student)
    changes = [e for e in events if e.delta]
    balance = student.balance
    for e in changes:
        e.after = balance
        balance -= e.delta
    return changes


async def _collect(session: AsyncSession, student: User) -> tuple[Summary, list[Event]]:
    """Итоги и все события ученика, новые сверху."""
    result = Summary()
    events: list[Event] = []

    submissions = (
        await session.scalars(
            select(Submission).where(
                Submission.user_id == student.id,
                Submission.status != SubmissionStatus.pending,
            )
        )
    ).unique()
    for s in submissions:
        title = s.quest.title
        if s.status == SubmissionStatus.approved:
            result.quests_done += 1
            result.earned += s.earned
            events.append(
                Event(
                    s.reviewed_at or s.created_at,
                    f"✅ «{title}» +{s.earned}",
                    s.earned,
                )
            )
        else:
            events.append(Event(s.reviewed_at or s.created_at, f"❌ «{title}» не засчитан"))

    orders = (await session.scalars(select(Order).where(Order.user_id == student.id))).unique()
    for o in orders:
        title = o.reward.title
        if o.status == OrderStatus.cancelled:
            events.append(Event(o.created_at, f"↩️ «{title}» — покупка отменена"))
            continue
        result.purchases += 1
        if o.reward.kind == RewardKind.lottery:
            won = o.prize_won or 0
            result.earned += max(won, 0)  # провал не уменьшает «заработано»
            outcome = (
                f"выигрыш +{won}" if won > 0
                else f"критический провал −{-won}" if won < 0
                else "без выигрыша"
            )
            events.append(
                Event(o.created_at, f"🎡 «{title}» −{o.price}, {outcome}", won - o.price)
            )
        else:
            events.append(Event(o.created_at, f"🎁 «{title}» −{o.price}", -o.price))

    transactions = await session.scalars(
        select(CoinTransaction).where(CoinTransaction.user_id == student.id)
    )
    for t in transactions:
        if t.amount > 0:
            result.earned += t.amount
        sign = "+" if t.amount > 0 else "−"
        events.append(Event(t.created_at, f"🪙 {sign}{abs(t.amount)} · {t.reason}", t.amount))

    # Только что добавленные записи — с часовым поясом, прочитанные из SQLite — без
    events.sort(key=lambda e: _utc(e.at), reverse=True)
    return result, events
