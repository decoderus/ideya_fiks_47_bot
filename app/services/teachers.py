"""Заявки на роль учителя и назначение учителям классов."""
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Role, SchoolClass, TeacherRequest, TeacherRequestStatus, User


class TeacherError(Exception):
    """Ошибка, текст которой можно показать пользователю."""


async def pending_request(session: AsyncSession, user: User) -> TeacherRequest | None:
    return await session.scalar(
        select(TeacherRequest).where(
            TeacherRequest.user_id == user.id,
            TeacherRequest.status == TeacherRequestStatus.pending,
        )
    )


async def create_request(
    session: AsyncSession, user: User, full_name: str, class_ids: list[int] | None = None
) -> TeacherRequest:
    if await pending_request(session, user) is not None:
        raise TeacherError("Заявка уже отправлена и ждёт рассмотрения.")
    request = TeacherRequest(user_id=user.id, full_name=full_name, class_ids=class_ids or [])
    session.add(request)
    user.state = None
    user.state_data = None
    await session.commit()
    return request


async def withdraw(session: AsyncSession, user: User) -> None:
    request = await pending_request(session, user)
    if request is not None:
        request.status = TeacherRequestStatus.withdrawn
        request.handled_at = datetime.now(timezone.utc)
        await session.commit()


async def pending_requests(session: AsyncSession) -> list[TeacherRequest]:
    return list(
        (
            await session.scalars(
                select(TeacherRequest)
                .where(TeacherRequest.status == TeacherRequestStatus.pending)
                .order_by(TeacherRequest.id)
            )
        ).unique()
    )


async def _handle(
    session: AsyncSession, admin: User, request_id: int, status: TeacherRequestStatus
) -> TeacherRequest:
    if admin.role not in (Role.admin, Role.moderator):
        raise TeacherError("Заявки рассматривают админ и модераторы.")
    request = await session.get(TeacherRequest, request_id)
    if request is None:
        raise TeacherError("Заявка не найдена.")
    if request.status != TeacherRequestStatus.pending:
        raise TeacherError("Заявка уже рассмотрена или отозвана.")
    request.status = status
    request.handled_by = admin.id
    request.handled_at = datetime.now(timezone.utc)
    if status == TeacherRequestStatus.approved:
        user = request.user
        user.full_name = request.full_name
        user.role = Role.teacher
        user.class_id = None
        user.state = None
        user.state_data = None
        # Классы — из заявки (выбор учителя, возможно, исправленный проверяющим)
        wanted = set(request.class_ids or [])
        user.teacher_classes = [
            c for c in await session.scalars(select(SchoolClass).order_by(SchoolClass.name))
            if c.id in wanted
        ]
    await session.commit()
    return request


async def approve(session: AsyncSession, admin: User, request_id: int) -> TeacherRequest:
    """Пользователь становится учителем, классы админ назначает следом."""
    return await _handle(session, admin, request_id, TeacherRequestStatus.approved)


async def reject(session: AsyncSession, admin: User, request_id: int) -> TeacherRequest:
    return await _handle(session, admin, request_id, TeacherRequestStatus.rejected)


async def teachers(session: AsyncSession) -> list[User]:
    return list(
        (
            await session.scalars(
                select(User)
                .where(User.role == Role.teacher, User.full_name.is_not(None))
                .order_by(User.full_name)
            )
        ).unique()
    )


async def get_teacher(session: AsyncSession, teacher_id: int) -> User:
    teacher = await session.get(User, teacher_id)
    if teacher is None or teacher.role != Role.teacher:
        raise TeacherError("Учитель не найден.")
    return teacher


async def toggle_class(
    session: AsyncSession, admin: User, teacher_id: int, class_id: int
) -> tuple[User, bool]:
    """Добавляет класс учителю или убирает. Возвращает (учитель, класс теперь назначен)."""
    if admin.role not in (Role.admin, Role.moderator):
        raise TeacherError("Классы учителям назначают админ и модераторы.")
    teacher = await get_teacher(session, teacher_id)
    school_class = await session.get(SchoolClass, class_id)
    if school_class is None:
        raise TeacherError("Класс не найден.")
    if school_class in teacher.teacher_classes:
        teacher.teacher_classes.remove(school_class)
        assigned = False
    else:
        teacher.teacher_classes.append(school_class)
        assigned = True
    await session.commit()
    return teacher, assigned


async def undo_approval(session: AsyncSession, admin: User, request_id: int) -> TeacherRequest:
    """Админ отменяет подтверждение учителя, сделанное модератором.

    Пользователь теряет роль учителя и классы и при следующем сообщении боту
    проходит регистрацию заново.
    """
    if admin.role != Role.admin:
        raise TeacherError("Отменить подтверждение может только админ.")
    request = await session.get(TeacherRequest, request_id)
    if request is None or request.status != TeacherRequestStatus.approved:
        raise TeacherError("Заявка не найдена или уже отменена.")
    user = request.user
    request.status = TeacherRequestStatus.rejected
    request.handled_by = admin.id
    request.handled_at = datetime.now(timezone.utc)
    if user.role == Role.teacher:
        user.role = Role.student
        user.teacher_classes.clear()
        user.class_id = None
        user.full_name = None
        user.state = None
        user.state_data = None
    await session.commit()
    return request


async def toggle_request_class(
    session: AsyncSession, reviewer: User, request_id: int, class_id: int
) -> TeacherRequest:
    """Проверяющий меняет классы в заявке до подтверждения."""
    if reviewer.role not in (Role.admin, Role.moderator):
        raise TeacherError("Заявки рассматривают админ и модераторы.")
    request = await session.get(TeacherRequest, request_id)
    if request is None or request.status != TeacherRequestStatus.pending:
        raise TeacherError("Заявка уже рассмотрена или отозвана.")
    if await session.get(SchoolClass, class_id) is None:
        raise TeacherError("Класс не найден.")
    ids = list(request.class_ids or [])
    ids.remove(class_id) if class_id in ids else ids.append(class_id)
    request.class_ids = ids  # новый список — чтобы изменение JSON сохранилось
    await session.commit()
    return request


async def class_names(session: AsyncSession, class_ids: list[int] | None) -> str:
    if not class_ids:
        return "не выбраны"
    classes = await session.scalars(
        select(SchoolClass).where(SchoolClass.id.in_(class_ids)).order_by(SchoolClass.name)
    )
    return ", ".join(c.name for c in classes) or "не выбраны"
