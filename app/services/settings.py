"""Настройки школы, которые админ переключает командами в боте."""
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AppSetting

# Модераторы временно проверяют ответы на квесты (пока в школе мало учителей)
MODERATOR_REVIEW = "moderator_review"


async def get_flag(session: AsyncSession, key: str) -> bool:
    setting = await session.get(AppSetting, key)
    return setting is not None and setting.value == "1"


async def set_flag(session: AsyncSession, key: str, value: bool) -> None:
    setting = await session.get(AppSetting, key)
    if setting is None:
        session.add(AppSetting(key=key, value="1" if value else "0"))
    else:
        setting.value = "1" if value else "0"
    await session.commit()


async def moderator_review(session: AsyncSession) -> bool:
    return await get_flag(session, MODERATOR_REVIEW)


# Канал школы, куда бот публикует лучшие ответы: chat_id и название
CHANNEL_ID = "channel_id"
CHANNEL_TITLE = "channel_title"


async def get_value(session: AsyncSession, key: str) -> str | None:
    setting = await session.get(AppSetting, key)
    return setting.value if setting is not None else None


async def set_value(session: AsyncSession, key: str, value: str) -> None:
    setting = await session.get(AppSetting, key)
    if setting is None:
        session.add(AppSetting(key=key, value=value))
    else:
        setting.value = value
    await session.commit()


async def channel(session: AsyncSession) -> tuple[int, str] | None:
    """Подключённый канал школы: (chat_id, название) или None."""
    chat_id = await get_value(session, CHANNEL_ID)
    if not chat_id:
        return None
    return int(chat_id), await get_value(session, CHANNEL_TITLE) or "канал школы"


async def set_channel(session: AsyncSession, chat_id: int, title: str) -> None:
    await set_value(session, CHANNEL_ID, str(chat_id))
    await set_value(session, CHANNEL_TITLE, title[:256] or "канал школы")
