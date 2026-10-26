"""Записи об успехах учеников («📒 Журнал успехов»).

Любой учитель и админ отмечает успех любого ученика школы: категория, текст и, по желанию,
монеты — только если ему и так можно начислять этому ученику (свой класс, лимиты учителя).
Ученик получает уведомление и видит свои успехи в профиле. Модераторы записи смотрят.
"""
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Role, Success, User
from app.services import coins as coins_service
from app.services.coins import CoinsError


@dataclass(frozen=True)
class Category:
    key: str
    emoji: str
    title: str


CATEGORIES = (
    Category("study", "📚", "Учёба"),
    Category("olymp", "🏆", "Олимпиады и конкурсы"),
    Category("sport", "⚽", "Спорт"),
    Category("art", "🎨", "Творчество"),
    Category("social", "🤝", "Общественная жизнь"),
    Category("other", "⭐", "Другое"),
)
BY_KEY = {c.key: c for c in CATEGORIES}
TEXT_MAX = 500
# Варианты монет к записи: в пределах лимита учителя за раз
COIN_CHOICES = (20, 50, 100, 150)


class SuccessError(Exception):
    """Ошибка, текст которой можно показать пользователю."""


def can_write(user: User) -> bool:
    return user.role in (Role.teacher, Role.admin)


def can_view(user: User) -> bool:
    return user.role in (Role.teacher, Role.admin, Role.moderator)


def can_delete(user: User, success: Success) -> bool:
    return user.role == Role.admin or success.author_id == user.id


def can_add_coins(author: User, student: User) -> bool:
    """Монеты к записи — по обычным правилам начисления (свой класс у учителя)."""
    return coins_service.can_manage(author, student)


def coin_choices(author: User) -> list[int]:
    limit = coins_service.per_operation_limit(author)
    return [c for c in COIN_CHOICES if c <= limit]


def label(success: Success) -> str:
    cat = BY_KEY.get(success.category, BY_KEY["other"])
    return f"{cat.emoji} {cat.title}"


async def get_student(session: AsyncSession, student_id: int) -> User:
    student = await session.get(User, student_id)
    if student is None or student.role != Role.student or student.full_name is None:
        raise SuccessError("Ученик не найден.")
    return student


async def add(
    session: AsyncSession,
    author: User,
    student_id: int,
    category: str,
    text: str,
    coins: int = 0,
) -> Success:
    if not can_write(author):
        raise SuccessError("Отмечать успехи могут учителя и администрация.")
    student = await get_student(session, student_id)
    if category not in BY_KEY:
        raise SuccessError("Выберите категорию.")
    text = (text or "").strip()
    if not text:
        raise SuccessError("Опишите успех текстом.")
    if len(text) > TEXT_MAX:
        raise SuccessError(f"Слишком длинно, максимум {TEXT_MAX} символов.")
    if coins:
        if not can_add_coins(author, student):
            raise SuccessError("Монеты этому ученику начисляет учитель его класса.")
        try:
            await coins_service.apply(session, author, student.id, coins, f"🌟 Успех: {text}")
        except CoinsError as exc:
            raise SuccessError(str(exc)) from exc
    success = Success(
        student_id=student.id,
        author_id=author.id,
        author_role=author.role,
        category=category,
        text=text,
        coins=coins,
    )
    session.add(success)
    await session.commit()
    # Новый объект — без связей: в асинхронном коде их нужно загрузить явно
    await session.refresh(success, ["student", "author"])
    return success


async def get(session: AsyncSession, success_id: int) -> Success:
    success = await session.get(Success, success_id)
    if success is None:
        raise SuccessError("Запись не найдена.")
    return success


async def delete(session: AsyncSession, user: User, success_id: int) -> Success:
    """Удалить ошибочную запись: автор или админ. Начисленные монеты не возвращаются."""
    success = await get(session, success_id)
    if not can_delete(user, success):
        raise SuccessError("Удалить запись может её автор или администратор.")
    await session.delete(success)
    await session.commit()
    return success


async def for_student(
    session: AsyncSession, student_id: int, offset: int = 0, limit: int = 100
) -> tuple[list[Success], bool]:
    rows = list(
        (
            await session.scalars(
                select(Success)
                .where(Success.student_id == student_id)
                .order_by(Success.created_at.desc(), Success.id.desc())
                .offset(offset)
                .limit(limit + 1)
            )
        ).unique()
    )
    return rows[:limit], len(rows) > limit


async def recent(session: AsyncSession, offset: int, limit: int) -> tuple[list[Success], bool]:
    rows = list(
        (
            await session.scalars(
                select(Success)
                .order_by(Success.created_at.desc(), Success.id.desc())
                .offset(offset)
                .limit(limit + 1)
            )
        ).unique()
    )
    return rows[:limit], len(rows) > limit


async def counts(session: AsyncSession, student_ids: list[int]) -> dict[int, int]:
    """Сколько записей у каждого ученика."""
    if not student_ids:
        return {}
    rows = await session.execute(
        select(Success.student_id, func.count())
        .where(Success.student_id.in_(student_ids))
        .group_by(Success.student_id)
    )
    return dict(rows.all())


async def by_category(session: AsyncSession, student_ids: list[int]) -> Counter:
    """Записи по категориям для ученика или класса."""
    if not student_ids:
        return Counter()
    rows = await session.execute(
        select(Success.category, func.count())
        .where(Success.student_id.in_(student_ids))
        .group_by(Success.category)
    )
    return Counter(dict(rows.all()))


def category_line(counter: Counter) -> str:
    """«📚 5 · 🏆 2 · ⚽ 3» — только непустые категории, в порядке CATEGORIES."""
    parts = [f"{c.emoji} {counter[c.key]}" for c in CATEGORIES if counter.get(c.key)]
    return " · ".join(parts)


async def total(session: AsyncSession) -> int:
    return await session.scalar(select(func.count()).select_from(Success)) or 0
