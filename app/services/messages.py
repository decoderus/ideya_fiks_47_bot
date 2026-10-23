"""Учёт сообщений бота, чтобы при перезапуске их можно было удалить."""
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BotMessage

# Сколько последних сообщений удаляем при перезапуске
RESTART_LIMIT = 100


async def remember(session: AsyncSession, max_user_id: int, mid: str) -> None:
    session.add(BotMessage(max_user_id=max_user_id, mid=mid))
    await session.commit()


async def pop_all(session: AsyncSession, max_user_id: int) -> list[str]:
    """Забирает id сообщений пользователя (новые первыми) и забывает их все."""
    mids = list(
        await session.scalars(
            select(BotMessage.mid)
            .where(BotMessage.max_user_id == max_user_id)
            .order_by(BotMessage.id.desc())
            .limit(RESTART_LIMIT)
        )
    )
    await session.execute(delete(BotMessage).where(BotMessage.max_user_id == max_user_id))
    await session.commit()
    return mids
