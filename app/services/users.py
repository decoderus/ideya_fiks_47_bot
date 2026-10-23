import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import EnrollmentLog, Role, SchoolClass, User

_NAME_PART = re.compile(r"[A-Za-zА-Яа-яЁё]+(-[A-Za-zА-Яа-яЁё]+)*")


async def get_or_create_user(
    session: AsyncSession, max_user_id: int, name: str, admin_ids: set[int]
) -> User:
    """Находит пользователя по MAX user_id или создаёт.

    Первичная роль: admin, если id в ADMIN_IDS, иначе student.
    Роль админа из ADMIN_IDS подтверждается при каждом входе.
    У админа нет классов: он работает со всей школой.
    """
    user = await session.scalar(select(User).where(User.max_user_id == max_user_id))
    if user is None:
        user = User(
            max_user_id=max_user_id,
            name=name,
            role=Role.admin if max_user_id in admin_ids else Role.student,
        )
        session.add(user)
    else:
        if name and user.name != name:
            user.name = name
        if max_user_id in admin_ids:
            _make_admin(user)
    await session.commit()
    return user


def _make_admin(user: User) -> None:
    user.role = Role.admin
    user.school_class = None
    if user.teacher_classes:
        user.teacher_classes.clear()


def normalize_full_name(text: str) -> str | None:
    """«иванов  иван» -> «Иванов Иван». None, если это не похоже на ФИО."""
    parts = text.split()
    if not 2 <= len(parts) <= 3 or len(text) > 100:
        return None
    if not all(_NAME_PART.fullmatch(p) for p in parts):
        return None
    return " ".join("-".join(w.capitalize() for w in p.split("-")) for p in parts)


async def register(session: AsyncSession, user: User, full_name: str) -> None:
    """Завершает регистрацию без класса (админ)."""
    user.full_name = full_name
    user.state = None
    user.state_data = None
    await session.commit()


async def enroll(
    session: AsyncSession, user: User, full_name: str, school_class: SchoolClass
) -> None:
    """Завершает регистрацию ученика: сразу зачисляет в выбранный класс.

    Админ и учитель класса потом могут отменить зачисление.
    """
    user.full_name = full_name
    user.role = Role.student
    user.school_class = school_class
    user.state = None
    user.state_data = None
    session.add(
        EnrollmentLog(
            user_id=user.id,
            class_id=school_class.id,
            full_name=full_name,
            class_name=school_class.name,
        )
    )
    await session.commit()


async def transfer(session: AsyncSession, student: User, school_class: SchoolClass) -> None:
    student.school_class = school_class
    await session.commit()


async def remove_student(session: AsyncSession, student: User) -> None:
    """Отменяет регистрацию ученика (например, посторонний человек).

    Монеты обнуляются, история квестов и покупок остаётся в базе.
    Чтобы снова пользоваться ботом, нужно заново пройти регистрацию.
    """
    student.full_name = None
    student.school_class = None
    student.balance = 0
    student.state = None
    student.state_data = None
    await session.commit()


async def set_state(
    session: AsyncSession, user: User, state: str | None, data: dict | None = None
) -> None:
    user.state = state
    user.state_data = data
    await session.commit()


async def set_role(session: AsyncSession, max_user_id: int, role: Role) -> User | None:
    user = await session.scalar(select(User).where(User.max_user_id == max_user_id))
    if user is None:
        return None
    if role == Role.admin:
        _make_admin(user)
    elif role == Role.moderator:
        # Модератор — служебная роль: перестаёт быть учеником
        user.role = Role.moderator
        user.school_class = None
        if user.teacher_classes:
            user.teacher_classes.clear()
    elif role == Role.teacher:
        # Классы учителю назначает админ в разделе «Учителя»
        user.role = Role.teacher
        user.school_class = None
    else:
        user.role = Role.student
        if user.teacher_classes:
            user.teacher_classes.clear()
        if user.class_id is None:
            # Ученику нужен класс: пусть пройдёт регистрацию и выберет его
            user.full_name = None
    await session.commit()
    return user


async def admins(session: AsyncSession) -> list[User]:
    return list((await session.scalars(select(User).where(User.role == Role.admin))).unique())


async def moderators(session: AsyncSession) -> list[User]:
    return list(
        (await session.scalars(select(User).where(User.role == Role.moderator))).unique()
    )
