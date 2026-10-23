"""Классы школы: создание админом, группы для выбора при регистрации, учителя класса."""
import re

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Broadcast,
    EnrollmentLog,
    Quest,
    Role,
    SchoolClass,
    User,
    teacher_classes,
)

NAME_MAX = 32

# Группы для выбора класса при регистрации: (ключ, название, параллели)
CATEGORIES = (
    ("1-4", "1–4 классы", range(1, 5)),
    ("5-9", "5–9 классы", range(5, 10)),
    ("10-11", "10–11 классы", range(10, 12)),
)
OTHER = ("other", "Другие")  # классы, в названии которых нет номера параллели

_CLASS_NAME = re.compile(r"(\d{1,2})\s*-?\s*([А-ЯЁA-Z])")
_GRADE = re.compile(r"\s*(\d{1,2})")


def teachers_of(class_id: int):
    """Условие «пользователь — учитель этого класса» для запросов."""
    return (User.role == Role.teacher) & User.id.in_(
        select(teacher_classes.c.teacher_id).where(teacher_classes.c.class_id == class_id)
    )


class ClassError(Exception):
    """Ошибка, текст которой можно показать пользователю."""


def grade_of(school_class: SchoolClass) -> int | None:
    match = _GRADE.match(school_class.name)
    return int(match.group(1)) if match else None


def category_of(school_class: SchoolClass) -> str:
    grade = grade_of(school_class)
    for key, _, grades in CATEGORIES:
        if grade in grades:
            return key
    return OTHER[0]


def sort_key(school_class: SchoolClass) -> tuple:
    grade = grade_of(school_class)
    return (grade if grade is not None else 99, school_class.name)


def normalize_name(text: str) -> str:
    """«5а», «5 А», «5-а» -> «5А». Параллели с 1 по 11."""
    match = _CLASS_NAME.fullmatch(text.strip().upper())
    if not match or not 1 <= int(match.group(1)) <= 11:
        raise ClassError(f"«{text.strip()}» — не похоже на класс. Пример: 5А")
    return f"{int(match.group(1))}{match.group(2)}"


async def all_classes(session: AsyncSession) -> list[SchoolClass]:
    return sorted(await session.scalars(select(SchoolClass)), key=sort_key)


async def categories(session: AsyncSession) -> list[tuple[str, str]]:
    """Непустые группы классов: [(ключ, название)]."""
    present = {category_of(c) for c in await all_classes(session)}
    groups = [(key, title) for key, title, _ in CATEGORIES if key in present]
    if OTHER[0] in present:
        groups.append(OTHER)
    return groups


async def in_category(session: AsyncSession, key: str) -> list[SchoolClass]:
    return [c for c in await all_classes(session) if category_of(c) == key]


async def add_classes(session: AsyncSession, text: str) -> tuple[list[str], list[str]]:
    """Добавляет классы из строки «5А 5Б, 6А». Возвращает (добавленные, уже были)."""
    names = []
    for part in re.split(r"[,;\s]+(?=\d)", text.strip()):
        if part.strip():
            name = normalize_name(part)
            if name not in names:
                names.append(name)
    if not names:
        raise ClassError("Напишите классы, например: 5А 5Б 6А")
    existing = set(await session.scalars(select(SchoolClass.name)))
    added = [n for n in names if n not in existing]
    session.add_all(SchoolClass(name=n) for n in added)
    await session.commit()
    return added, [n for n in names if n in existing]


async def get(session: AsyncSession, class_id: int) -> SchoolClass:
    school_class = await session.get(SchoolClass, class_id)
    if school_class is None:
        raise ClassError("Класс не найден.")
    return school_class


async def rename(session: AsyncSession, class_id: int, text: str) -> SchoolClass:
    school_class = await get(session, class_id)
    name = text.strip()
    try:
        name = normalize_name(name)
    except ClassError:
        # Особые названия («Тестовый класс») тоже допустимы, но без номера
        # параллели класс попадёт в группу «Другие»
        if not name or len(name) > NAME_MAX:
            raise ClassError(f"Название — от 1 до {NAME_MAX} символов.")
    duplicate = await session.scalar(
        select(SchoolClass).where(SchoolClass.name == name, SchoolClass.id != class_id)
    )
    if duplicate is not None:
        raise ClassError(f"Класс «{name}» уже есть.")
    school_class.name = name
    await session.commit()
    return school_class


async def students(session: AsyncSession, class_id: int) -> list[User]:
    return list(
        (
            await session.scalars(
                select(User)
                .where(
                    User.role == Role.student,
                    User.full_name.is_not(None),
                    User.class_id == class_id,
                )
                .order_by(User.full_name)
            )
        ).unique()
    )


async def teachers(session: AsyncSession, class_id: int) -> list[User]:
    return list(
        (
            await session.scalars(
                select(User)
                .join(teacher_classes, teacher_classes.c.teacher_id == User.id)
                .where(teacher_classes.c.class_id == class_id, User.role == Role.teacher)
                .order_by(User.full_name)
            )
        ).unique()
    )


async def delete(session: AsyncSession, class_id: int) -> str:
    """Удаляет пустой класс. Возвращает его название."""
    school_class = await get(session, class_id)
    if await students(session, class_id):
        raise ClassError("В классе есть ученики: сначала переведите их.")
    for model in (Quest, Broadcast):
        used = await session.scalar(
            select(func.count()).select_from(model).where(model.class_id == class_id)
        )
        if used:
            raise ClassError("Класс уже использовался в квестах или оповещениях, удалить нельзя.")
    # Ученики, не завершившие регистрацию, могли остаться с этим классом
    for user in await session.scalars(select(User).where(User.class_id == class_id)):
        user.class_id = None
    await session.execute(teacher_classes.delete().where(teacher_classes.c.class_id == class_id))
    await session.execute(
        update(EnrollmentLog).where(EnrollmentLog.class_id == class_id).values(class_id=None)
    )
    name = school_class.name
    await session.delete(school_class)
    await session.commit()
    return name
