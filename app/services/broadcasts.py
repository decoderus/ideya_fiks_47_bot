"""Оповещения: кому отправлять, сохранение и история."""
from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Broadcast,
    BroadcastDraft,
    BroadcastRecipient,
    CoinTransaction,
    DraftStatus,
    Role,
    SchoolClass,
    User,
)
from app.services.classes import teachers_of

# С запасом до лимита MAX в 4000 символов: добавляем заголовок
TEXT_MAX = 3500

# Монет ученику за прочтение оповещения (один раз за каждое)
READ_REWARD = 10


class BroadcastError(Exception):
    """Ошибка, текст которой можно показать пользователю."""


def can_broadcast(user: User) -> bool:
    """Отправлять сразу может только админ."""
    return user.role == Role.admin


def can_prepare(user: User) -> bool:
    """Готовить оповещения: админ — и сразу отправляет, модератор — на подтверждение."""
    return user.role in (Role.admin, Role.moderator)


# Кому оповещение (Broadcast.audience): all — ученики и учителя, students, teachers,
# moderators, class:<id> — ученики класса, user:<id> — один ученик, учитель или модератор.
# classall:<id> — ученики и учителя класса: так работали оповещения классу раньше
AUDIENCE_TITLES = {
    "all": "вся школа (ученики и учителя)",
    "students": "все ученики",
    "teachers": "все учителя",
    "moderators": "модераторы",
}


# Кому можно написать лично
PERSONAL_ROLES = {Role.student: "ученик", Role.teacher: "учитель", Role.moderator: "модератор"}


def legacy_audience(class_id: int | None) -> str:
    """Аудитория старых оповещений, сохранённых до выбора аудитории."""
    return "all" if class_id is None else f"classall:{class_id}"


def _split(audience: str) -> tuple[str, int | None]:
    kind, _, value = audience.partition(":")
    if kind in AUDIENCE_TITLES and not value:
        return kind, None
    if kind in ("class", "classall", "user"):
        try:
            return kind, int(value)
        except ValueError:
            pass
    raise BroadcastError("Неизвестные получатели.")


async def check_audience(session: AsyncSession, audience: str) -> tuple[str, int | None]:
    """Описание получателей и класс (для class/user) — или BroadcastError."""
    kind, value = _split(audience)
    if kind in AUDIENCE_TITLES:
        return AUDIENCE_TITLES[kind], None
    if kind == "user":
        person = await session.get(User, value)
        if person is None or person.role not in PERSONAL_ROLES or person.full_name is None:
            raise BroadcastError("Получатель не найден.")
        klass = f" ({person.school_class.name})" if person.school_class else ""
        return f"{PERSONAL_ROLES[person.role]} {person.full_name}{klass}", person.class_id
    school_class = await check_class(session, value)
    if kind == "class":
        return f"ученики класса «{school_class.name}»", school_class.id
    return f"класс «{school_class.name}» (ученики и учителя)", school_class.id


async def describe(session: AsyncSession, audience: str | None, class_id: int | None = None) -> str:
    """Описание получателей для истории; удалённый класс или ученик не ломают показ."""
    try:
        return (await check_audience(session, audience or legacy_audience(class_id)))[0]
    except BroadcastError:
        return "получатели удалены"


# Строка «Кому:» в шапке оповещения — так её видит получатель
READER_TITLES = {
    "all": "всем ученикам и учителям",
    "students": "всем ученикам",
    "teachers": "учителям",
    "moderators": "модераторам",
}


async def reader_label(session: AsyncSession, audience: str | None, class_id: int | None = None) -> str:
    try:
        kind, value = _split(audience or legacy_audience(class_id))
    except BroadcastError:
        return "школе"
    if kind in READER_TITLES:
        return READER_TITLES[kind]
    if kind == "user":
        return "лично вам"
    school_class = await session.get(SchoolClass, value)
    name = f"«{school_class.name}»" if school_class else ""
    return f"ученикам класса {name}".strip() if kind == "class" else f"классу {name}".strip()


async def recipients(session: AsyncSession, audience: str, author: User) -> list[User]:
    """Зарегистрированные получатели оповещения, кроме автора."""
    kind, value = _split(audience)
    query = select(User).where(User.full_name.is_not(None), User.id != author.id)
    if kind == "all":
        query = query.where(User.role.in_((Role.student, Role.teacher)))
    elif kind == "students":
        query = query.where(User.role == Role.student)
    elif kind == "teachers":
        query = query.where(User.role == Role.teacher)
    elif kind == "moderators":
        query = query.where(User.role == Role.moderator)
    elif kind == "class":
        query = query.where(User.role == Role.student, User.class_id == value)
    elif kind == "user":
        query = query.where(User.role.in_(tuple(PERSONAL_ROLES)), User.id == value)
    else:  # classall
        query = query.where(
            or_((User.role == Role.student) & (User.class_id == value), teachers_of(value))
        )
    return list((await session.scalars(query.order_by(User.id))).unique())


async def check_class(session: AsyncSession, class_id: int | None) -> SchoolClass | None:
    if class_id is None:
        return None
    school_class = await session.get(SchoolClass, class_id)
    if school_class is None:
        raise BroadcastError("Класс не найден.")
    return school_class


async def people(session: AsyncSession, role: Role) -> list[User]:
    """Зарегистрированные учителя или модераторы по алфавиту — для выбора адресата."""
    rows = await session.scalars(
        select(User).where(User.role == role, User.full_name.is_not(None)).order_by(User.full_name)
    )
    return list(rows.unique())


async def class_students(session: AsyncSession, class_id: int) -> list[User]:
    """Зарегистрированные ученики класса по алфавиту — для выбора адресата."""
    rows = await session.scalars(
        select(User)
        .where(User.role == Role.student, User.class_id == class_id, User.full_name.is_not(None))
        .order_by(User.full_name)
    )
    return list(rows.unique())


async def create(
    session: AsyncSession,
    author: User,
    text: str,
    photo: dict | None,
    audience: str,
    targets: list[User],
    approved_by: User | None = None,
    audio: dict | None = None,
) -> Broadcast:
    """Сохраняет оповещение и список получателей (для их истории).

    Оповещение модератора сохраняется, только если его подтвердил админ (approved_by).
    """
    approved = approved_by is not None and approved_by.role == Role.admin
    if not (can_broadcast(author) or (author.role == Role.moderator and approved)):
        raise BroadcastError("Оповещения отправляет админ; модератор — после его подтверждения.")
    if not text and not photo and not audio:
        raise BroadcastError("Оповещение пустое.")
    _, class_id = await check_audience(session, audience)
    broadcast = Broadcast(
        author_id=author.id,
        text=text[:TEXT_MAX],
        photo=photo,
        audio=audio,
        class_id=class_id,
        audience=audience,
        recipients=len(targets),
    )
    session.add(broadcast)
    await session.flush()
    session.add_all(BroadcastRecipient(broadcast_id=broadcast.id, user_id=t.id) for t in targets)
    await session.commit()
    return broadcast


async def history(
    session: AsyncSession, offset: int, limit: int
) -> tuple[list[Broadcast], bool]:
    rows = list(
        (
            await session.scalars(
                select(Broadcast).order_by(Broadcast.id.desc()).offset(offset).limit(limit + 1)
            )
        ).unique()
    )
    return rows[:limit], len(rows) > limit


# --- полученные оповещения ---


async def inbox(
    session: AsyncSession, user: User, offset: int, limit: int
) -> tuple[list[BroadcastRecipient], bool]:
    """Оповещения, полученные пользователем, новые сверху: (страница, есть ли ещё)."""
    rows = list(
        (
            await session.scalars(
                select(BroadcastRecipient)
                .where(BroadcastRecipient.user_id == user.id)
                .order_by(BroadcastRecipient.broadcast_id.desc())
                .offset(offset)
                .limit(limit + 1)
            )
        ).unique()
    )
    return rows[:limit], len(rows) > limit


async def unread_count(session: AsyncSession, user: User) -> int:
    return await session.scalar(
        select(func.count()).where(
            BroadcastRecipient.user_id == user.id, BroadcastRecipient.read_at.is_(None)
        )
    )


async def mark_read(
    session: AsyncSession, user: User, broadcast_id: int
) -> tuple[Broadcast | None, int]:
    """Отмечает оповещение прочитанным: (оповещение, начислено монет).

    Ученик получает READ_REWARD монет один раз за каждое оповещение — при первом
    прочтении, кнопкой «Прочитано» или открытием из истории.
    Оповещение None, если пользователь его не получал.
    """
    entry = await session.scalar(
        select(BroadcastRecipient).where(
            BroadcastRecipient.user_id == user.id,
            BroadcastRecipient.broadcast_id == broadcast_id,
        )
    )
    if entry is None:
        return None, 0
    reward = 0
    if entry.read_at is None:
        entry.read_at = datetime.now(timezone.utc)
        if user.role == Role.student:
            reward = READ_REWARD
            user.balance += reward
            session.add(
                CoinTransaction(
                    user_id=user.id,
                    actor_id=entry.broadcast.author_id,
                    amount=reward,
                    reason="Прочитано оповещение",
                )
            )
        await session.commit()
    return entry.broadcast, reward


# --- оповещения модераторов: только после подтверждения админа ---


async def create_draft(
    session: AsyncSession,
    author: User,
    text: str,
    photo: dict | None,
    audience: str,
    audio: dict | None = None,
) -> BroadcastDraft:
    if author.role != Role.moderator:
        raise BroadcastError("Черновики оповещений готовят модераторы.")
    if not text and not photo and not audio:
        raise BroadcastError("Оповещение пустое.")
    _, class_id = await check_audience(session, audience)
    draft = BroadcastDraft(
        author_id=author.id,
        text=text[:TEXT_MAX],
        photo=photo,
        audio=audio,
        class_id=class_id,
        audience=audience,
    )
    session.add(draft)
    await session.commit()
    return draft


async def pending_draft(session: AsyncSession, admin: User, draft_id: int) -> BroadcastDraft:
    if admin.role != Role.admin:
        raise BroadcastError("Подтверждать оповещения может только админ.")
    draft = await session.get(BroadcastDraft, draft_id)
    if draft is None:
        raise BroadcastError("Оповещение не найдено.")
    if draft.status != DraftStatus.pending:
        raise BroadcastError("Оповещение уже отправлено или отклонено.")
    return draft


async def close_draft(
    session: AsyncSession,
    draft: BroadcastDraft,
    admin: User,
    status: DraftStatus,
    broadcast: Broadcast | None = None,
) -> None:
    draft.status = status
    draft.handled_by = admin.id
    draft.handled_at = datetime.now(timezone.utc)
    draft.broadcast_id = broadcast.id if broadcast else None
    await session.commit()
