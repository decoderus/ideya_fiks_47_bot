"""Рейтинг учеников по текущему балансу монет."""
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SchoolClass
from app.services import seasons as seasons_service


@dataclass
class Entry:
    user_id: int
    name: str
    score: int  # текущий баланс
    place: int


async def leaderboard(session: AsyncSession, class_id: int | None = None) -> list[Entry]:
    """Все ученики школы (или класса) по убыванию баланса.

    Одинаковый баланс — одинаковое место: 1, 2, 2, 4.
    """
    students = await seasons_service.students(session, class_id)
    balances = {s.id: s.balance for s in students}
    return [
        Entry(s.user_id, s.name, s.points, s.place)
        for s in seasons_service.rank(students, balances)
    ]


async def classes(session: AsyncSession) -> list[SchoolClass]:
    return list(await session.scalars(select(SchoolClass).order_by(SchoolClass.name)))
