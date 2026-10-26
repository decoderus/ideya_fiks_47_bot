"""Выгрузка класса или всей школы в таблицу LibreOffice (.ods): сводка, успехи, движение монет.

Кто может: админ — любой класс и вся школа, учитель — только свои классы.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    CoinTransaction,
    Order,
    OrderStatus,
    RewardKind,
    Role,
    SchoolClass,
    Submission,
    SubmissionStatus,
    Success,
    User,
)
from app.services import ods
from app.services.successes import BY_KEY

MSK = timezone(timedelta(hours=3))
PERIODS = {7: "за 7 дней", 30: "за 30 дней", 0: "за всё время"}
ROLE_NAMES = {Role.admin: "админ", Role.moderator: "модератор", Role.teacher: "учитель", Role.student: "ученик"}


def can_export(user: User, class_id: int) -> bool:
    """class_id = 0 — вся школа."""
    if user.role == Role.admin:
        return True
    return user.role == Role.teacher and class_id != 0 and class_id in user.teacher_class_ids


def _local(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    moment = moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(MSK).replace(tzinfo=None)


@dataclass
class Export:
    filename: str
    data: bytes
    students: int
    successes: int
    movements: int


async def _students(session: AsyncSession, class_id: int) -> list[User]:
    query = select(User).where(User.role == Role.student, User.full_name.is_not(None))
    if class_id:
        query = query.where(User.class_id == class_id)
    rows = await session.scalars(query)
    return sorted(rows.unique(), key=lambda u: (u.school_class.name if u.school_class else "", u.full_name))


async def build(session: AsyncSession, class_id: int, days: int, now: datetime | None = None) -> Export | None:
    now = now or datetime.now(timezone.utc)
    if class_id:
        school_class = await session.get(SchoolClass, class_id)
        if school_class is None:
            return None
        title = school_class.name
    else:
        title = "вся школа"
    since = now - timedelta(days=days) if days else None
    students = await _students(session, class_id)
    ids = [s.id for s in students]
    by_id = {s.id: s for s in students}

    def in_period(moment: datetime | None) -> bool:
        if moment is None:
            return False
        moment = moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
        return since is None or moment >= since

    def class_of(user: User) -> str:
        return user.school_class.name if user.school_class else ""

    submissions = list((await session.scalars(select(Submission).where(Submission.user_id.in_(ids)))).unique())
    transactions = list(
        (await session.scalars(select(CoinTransaction).where(CoinTransaction.user_id.in_(ids)))).unique()
    )
    orders = list((await session.scalars(select(Order).where(Order.user_id.in_(ids)))).unique())
    successes = list(
        (
            await session.scalars(
                select(Success).where(Success.student_id.in_(ids)).order_by(Success.created_at.desc())
            )
        ).unique()
    )

    # Движение монет: квесты, ручные начисления, покупки и колесо фортуны
    movements: list[tuple] = []  # (когда, ученик, сумма, откуда, кто)
    for sub in submissions:
        when = sub.reviewed_at or sub.created_at
        if sub.status == SubmissionStatus.approved and sub.reward and in_period(when):
            movements.append((when, by_id[sub.user_id], sub.reward, f"Квест «{sub.quest.title}»", ""))
    for t in transactions:
        if in_period(t.created_at):
            who = f"{t.actor.display_name} ({ROLE_NAMES[t.actor.role]})" if t.actor_id != t.user_id else ""
            movements.append((t.created_at, by_id[t.user_id], t.amount, t.reason, who))
    for o in orders:
        if o.status == OrderStatus.cancelled or not in_period(o.created_at):
            continue
        movements.append((o.created_at, by_id[o.user_id], -o.price, f"Покупка «{o.reward.title}»", ""))
        if o.reward.kind == RewardKind.lottery and o.prize_won:
            result = "выигрыш" if o.prize_won > 0 else "провал"
            movements.append((o.created_at, by_id[o.user_id], o.prize_won, f"{o.reward.title}: {result}", ""))
    movements.sort(key=lambda m: m[0], reverse=True)

    # Сводка по ученикам
    stats = {
        s.id: {"quests": 0, "quest_coins": 0, "manual": 0, "spent": 0, "successes": 0, "last": None}
        for s in students
    }
    for sub in submissions:
        st = stats[sub.user_id]
        created = _local(sub.created_at)
        st["last"] = max(st["last"], created) if st["last"] else created
        if sub.status == SubmissionStatus.approved and in_period(sub.reviewed_at or sub.created_at):
            st["quests"] += 1
            st["quest_coins"] += sub.reward or 0
    for t in transactions:
        if in_period(t.created_at):
            stats[t.user_id]["manual"] += t.amount
    for o in orders:
        if o.status != OrderStatus.cancelled and in_period(o.created_at):
            stats[o.user_id]["spent"] += o.price
    period_successes = [s for s in successes if in_period(s.created_at)]
    for s in period_successes:
        stats[s.student_id]["successes"] += 1

    summary = ods.Sheet(
        "Сводка",
        ["Ученик", "Класс", "Баланс сейчас", "Квестов засчитано", "Монет за квесты",
         "Начислено вручную (±)", "Потрачено в магазине", "Успехов в журнале", "Последний ответ"],
        [6.5, 2, 3, 3.5, 3.5, 4.2, 4, 3.8, 4],
    )
    for s in students:
        st = stats[s.id]
        summary.rows.append([
            s.full_name, class_of(s), s.balance, st["quests"], st["quest_coins"],
            st["manual"], st["spent"], st["successes"], st["last"],
        ])

    journal = ods.Sheet(
        "Успехи",
        ["Дата", "Ученик", "Класс", "Область", "Что сделал", "Кто отметил", "Монеты"],
        [3.6, 6, 2, 4, 12, 6, 2.2],
        wrap={4},
    )
    for s in period_successes:
        cat = BY_KEY.get(s.category, BY_KEY["other"])
        journal.rows.append([
            _local(s.created_at), s.student.full_name, class_of(s.student), cat.title, s.text,
            f"{s.author.display_name} ({ROLE_NAMES[s.author_role]})", s.coins or None,
        ])

    coins = ods.Sheet(
        "Монеты",
        ["Дата", "Ученик", "Класс", "Сумма", "Откуда", "Кто начислил"],
        [3.6, 6, 2, 2.2, 10, 6],
        wrap={4},
    )
    for when, student, amount, source, who in movements:
        coins.rows.append([_local(when), student.full_name, class_of(student), amount, source, who])

    stamp = _local(now)
    name = title.replace(" ", "_")
    filename = f"Актив_{name}_{PERIODS[days].replace(' ', '_')}_{stamp:%Y-%m-%d}.ods"
    return Export(filename, ods.build([summary, journal, coins]), len(students), len(period_successes), len(movements))
