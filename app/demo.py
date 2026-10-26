"""Демо-режим (DEMO=true): для жюри и презентаций на обезличенной базе (deploy/anonymize.py).

- /demo — любой пользователь выбирает роль: ученик, учитель, модератор, админ;
- сообщения вымышленным пользователям (номер MAX от FAKE_ID_BASE) не отправляются;
- при первом запуске, если базы ещё нет, она копируется из DEMO_DB;
- на базе с настоящей школой (нет ни одного вымышленного пользователя) демо-режим не запускается.

Рабочий бот школы запускается без DEMO: тогда /demo не работает ни у кого, включая админа.
"""
import shutil
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CoinTransaction, Role, SchoolClass, User
from app.services import users as users_service

# Номера MAX вымышленных пользователей обезличенной базы: реальные номера намного меньше
FAKE_ID_BASE = 9_000_000_000
DEMO_NAME = "Проверяющий Жюри"
# Монеты ученику-проверяющему, чтобы было что потратить в магазине
START_BALANCE = 1000
START_REASON = "🧪 Демо: стартовый баланс"


def is_fake(max_user_id: int) -> bool:
    return max_user_id >= FAKE_ID_BASE


class RealDatabaseError(RuntimeError):
    """DEMO=true на базе с настоящими пользователями."""


async def check_database(session: AsyncSession) -> None:
    """Демо-режим разрешён только на обезличенной или пустой базе.

    /demo меняет роли и монеты прямо в базе: на настоящей школе это испортило бы рейтинг
    и соревнование. Обезличенная база узнаётся по вымышленным номерам MAX."""
    users = await session.scalar(select(func.count()).select_from(User)) or 0
    fake = await session.scalar(
        select(func.count()).select_from(User).where(User.max_user_id >= FAKE_ID_BASE)
    ) or 0
    if users and not fake:
        raise RealDatabaseError(
            "DEMO=true на базе с настоящими пользователями: демо-режим разрешён только "
            "на обезличенной базе (deploy/anonymize.py). Уберите DEMO из настроек."
        )


def seed_database(database_url: str, demo_db: str) -> bool:
    """Копирует демо-базу, если рабочей ещё нет (только SQLite). True — скопирована."""
    prefix = "sqlite+aiosqlite:///"
    if not demo_db or not database_url.startswith(prefix):
        return False
    target = Path(database_url[len(prefix):])
    if target.exists() or not Path(demo_db).exists():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(demo_db, target)
    return True


async def _busiest_classes(session: AsyncSession, limit: int) -> list[SchoolClass]:
    """Классы с наибольшим числом учеников: там больше всего данных для просмотра."""
    rows = await session.execute(
        select(SchoolClass, func.count(User.id).label("n"))
        .join(User, (User.class_id == SchoolClass.id) & (User.role == Role.student))
        .group_by(SchoolClass.id)
        .order_by(func.count(User.id).desc(), SchoolClass.name)
        .limit(limit)
    )
    return [c for c, _ in rows]


async def switch_role(session: AsyncSession, user: User, role: Role) -> User:
    """Сделать пользователя учеником, учителем, модератором или админом без подтверждений."""
    if user.full_name is None:
        # Имя и фамилия из профиля MAX; без них — условное имя
        user.full_name = user.name or DEMO_NAME
    user.state = None
    user.state_data = None
    classes = await _busiest_classes(session, 2)
    if role == Role.student:
        user.role = Role.student
        if user.teacher_classes:
            user.teacher_classes.clear()
        if classes:
            user.school_class = classes[0]
        if user.balance == 0:
            user.balance = START_BALANCE
            session.add(
                CoinTransaction(
                    user_id=user.id, actor_id=user.id, amount=START_BALANCE, reason=START_REASON
                )
            )
    elif role == Role.teacher:
        user.role = Role.teacher
        user.school_class = None
        # Два класса: видно и выбор класса для квеста
        user.teacher_classes = classes
    else:
        await users_service.set_role(session, user.max_user_id, role)
    await session.commit()
    return user
