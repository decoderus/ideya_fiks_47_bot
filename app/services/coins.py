"""Ручное начисление и списание монет.

Админ — без ограничений. Учитель — только ученикам своего класса, не больше
150 монет за раз и 1000 в сутки (начисления и списания вместе, сутки по Москве).
Баланс ученика не уходит в минус.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CoinTransaction, Role, User
from app.services import classes as classes_service

TEACHER_PER_OPERATION = 150
TEACHER_DAILY = 1000
# У админа лимита по смыслу нет, это лишь защита от опечатки в лишние нули
ADMIN_PER_OPERATION = 1_000_000
REASON_MAX = 200

MSK = timezone(timedelta(hours=3))


class CoinsError(Exception):
    """Ошибка, текст которой можно показать пользователю."""


def can_use(user: User) -> bool:
    return user.role in (Role.teacher, Role.admin)


def per_operation_limit(actor: User) -> int:
    return ADMIN_PER_OPERATION if actor.role == Role.admin else TEACHER_PER_OPERATION


def can_manage(actor: User, student: User) -> bool:
    if student.role != Role.student or student.full_name is None:
        return False
    if actor.role == Role.admin:
        return True
    return actor.role == Role.teacher and student.class_id in actor.teacher_class_ids


async def get_student(session: AsyncSession, actor: User, user_id: int) -> User:
    student = await session.get(User, user_id)
    if student is None or not can_manage(actor, student):
        raise CoinsError("Ученик не найден.")
    return student


def day_start(now: datetime) -> datetime:
    """Начало текущих суток по Москве, в UTC (так время хранится в базе)."""
    local = now.astimezone(MSK).replace(hour=0, minute=0, second=0, microsecond=0)
    return local.astimezone(timezone.utc)


async def used_today(session: AsyncSession, actor: User, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    return await session.scalar(
        select(func.coalesce(func.sum(func.abs(CoinTransaction.amount)), 0)).where(
            CoinTransaction.actor_id == actor.id,
            CoinTransaction.created_at >= day_start(now),
        )
    )


async def remaining_today(
    session: AsyncSession, actor: User, now: datetime | None = None
) -> int | None:
    """Сколько учитель ещё может начислить или списать сегодня. None — без лимита."""
    if actor.role == Role.admin:
        return None
    return max(TEACHER_DAILY - await used_today(session, actor, now), 0)


def parse_amount(text: str) -> int:
    try:
        amount = int(text.strip())
    except ValueError:
        raise CoinsError("Напишите целое число монет.")
    if amount <= 0:
        raise CoinsError("Число должно быть больше нуля.")
    return amount


async def check_amount(
    session: AsyncSession, actor: User, student: User, amount: int, now: datetime | None = None
) -> None:
    """amount со знаком: > 0 — начислить, < 0 — списать."""
    size = abs(amount)
    limit = per_operation_limit(actor)
    if not 1 <= size <= limit:
        raise CoinsError(f"За один раз можно от 1 до {limit} монет.")
    remaining = await remaining_today(session, actor, now)
    if remaining is not None and size > remaining:
        raise CoinsError(
            f"Дневной лимит: сегодня можно ещё {remaining} из {TEACHER_DAILY} монет."
        )
    if amount < 0 and student.balance < size:
        raise CoinsError(f"У ученика только {student.balance} монет, больше списать нельзя.")


async def apply(
    session: AsyncSession,
    actor: User,
    student_id: int,
    amount: int,
    reason: str,
    now: datetime | None = None,
) -> CoinTransaction:
    if not can_use(actor):
        raise CoinsError("Раздел доступен учителям и админам.")
    student = await get_student(session, actor, student_id)
    reason = reason.strip()
    if not reason:
        raise CoinsError("Укажите причину.")
    await check_amount(session, actor, student, amount, now)
    student.balance += amount
    transaction = CoinTransaction(
        user_id=student.id, actor_id=actor.id, amount=amount, reason=reason[:REASON_MAX]
    )
    if now is not None:
        transaction.created_at = now
    session.add(transaction)
    await session.commit()
    return transaction


async def check_class_amount(
    session: AsyncSession, actor: User, class_id: int, amount: int, now: datetime | None = None
) -> list[User]:
    """Проверить начисление amount каждому ученику класса. Возвращает учеников.

    Лимит учителя «за раз» — на одного ученика, в дневной лимит идёт вся сумма.
    """
    if not can_use(actor):
        raise CoinsError("Раздел доступен учителям и админам.")
    if actor.role != Role.admin and class_id not in actor.teacher_class_ids:
        raise CoinsError("Доступны только ваши классы.")
    limit = per_operation_limit(actor)
    if not 1 <= amount <= limit:
        raise CoinsError(f"Каждому ученику можно от 1 до {limit} монет.")
    students = await classes_service.students(session, class_id)
    if not students:
        raise CoinsError("В классе нет учеников.")
    remaining = await remaining_today(session, actor, now)
    total = amount * len(students)
    if remaining is not None and total > remaining:
        raise CoinsError(
            f"Всего выйдет {total} монет ({len(students)} × {amount}), а сегодня можно ещё "
            f"{remaining} из {TEACHER_DAILY}. Максимум каждому: {remaining // len(students)}."
        )
    return students


async def apply_class(
    session: AsyncSession,
    actor: User,
    class_id: int,
    amount: int,
    reason: str,
    now: datetime | None = None,
) -> list[User]:
    """Начислить amount каждому ученику класса одной операцией."""
    reason = reason.strip()
    if not reason:
        raise CoinsError("Укажите причину.")
    students = await check_class_amount(session, actor, class_id, amount, now)
    for student in students:
        student.balance += amount
        transaction = CoinTransaction(
            user_id=student.id, actor_id=actor.id, amount=amount, reason=reason[:REASON_MAX]
        )
        if now is not None:
            transaction.created_at = now
        session.add(transaction)
    await session.commit()
    return students


async def history(session: AsyncSession, student: User, limit: int = 5) -> list[CoinTransaction]:
    return list(
        (
            await session.scalars(
                select(CoinTransaction)
                .where(CoinTransaction.user_id == student.id)
                .order_by(CoinTransaction.id.desc())
                .limit(limit)
            )
        ).unique()
    )
