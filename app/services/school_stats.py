"""Журнал регистраций учеников и статистика пользователей (для админа)."""
from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import EnrollmentLog, Role, TeacherRequest, TeacherRequestStatus, User
from app.services import classes as classes_service


async def enrollments(
    session: AsyncSession, offset: int, limit: int
) -> tuple[list[EnrollmentLog], bool]:
    rows = list(
        await session.scalars(
            select(EnrollmentLog).order_by(EnrollmentLog.id.desc()).offset(offset).limit(limit + 1)
        )
    )
    return rows[:limit], len(rows) > limit


async def unchecked_count(session: AsyncSession) -> int:
    return await session.scalar(
        select(func.count()).select_from(EnrollmentLog).where(EnrollmentLog.checked_at.is_(None))
    )


async def check(session: AsyncSession, entry_id: int) -> EnrollmentLog | None:
    entry = await session.get(EnrollmentLog, entry_id)
    if entry is not None and entry.checked_at is None:
        entry.checked_at = datetime.now(timezone.utc)
        await session.commit()
    return entry


async def check_all(session: AsyncSession) -> int:
    result = await session.execute(
        update(EnrollmentLog)
        .where(EnrollmentLog.checked_at.is_(None))
        .values(checked_at=datetime.now(timezone.utc))
    )
    await session.commit()
    return result.rowcount


async def school(session: AsyncSession) -> dict:
    """Сводка по пользователям: роли, незавершённые регистрации, ученики по классам, учителя."""
    by_role = dict(
        (
            await session.execute(
                select(User.role, func.count())
                .where(User.full_name.is_not(None))
                .group_by(User.role)
            )
        ).all()
    )
    per_class = dict(
        (
            await session.execute(
                select(User.class_id, func.count())
                .where(User.role == Role.student, User.full_name.is_not(None))
                .group_by(User.class_id)
            )
        ).all()
    )
    teachers = list(
        (
            await session.scalars(
                select(User)
                .where(User.role == Role.teacher, User.full_name.is_not(None))
                .order_by(User.full_name)
            )
        ).unique()
    )
    return {
        "total": await session.scalar(select(func.count()).select_from(User)),
        "by_role": by_role,
        "unfinished": await session.scalar(
            select(func.count()).select_from(User).where(User.full_name.is_(None))
        ),
        "teacher_requests": await session.scalar(
            select(func.count())
            .select_from(TeacherRequest)
            .where(TeacherRequest.status == TeacherRequestStatus.pending)
        ),
        "classes": [(c, per_class.get(c.id, 0)) for c in await classes_service.all_classes(session)],
        "teachers": teachers,
    }
