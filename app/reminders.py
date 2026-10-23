"""Утренние напоминания о сериях: python -m app.reminders

Запускается таймером aktiv-reminders.timer каждое утро. Отправляет через Bot._send,
чтобы сообщения удалялись кнопкой «Перезапустить», как остальные сообщения бота.
"""
import asyncio
import logging

import httpx

from app import keyboards as kb
from app.bot import BROADCAST_INTERVAL, Bot
from app.config import get_settings
from app.db.session import make_engine, make_session_factory
from app.max_api import MaxClient
from app.services import reminders as reminders_service

log = logging.getLogger("aktiv.reminders")


async def run() -> None:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    client = MaxClient(settings.bot_token, settings.max_api_base, settings.ca_bundle)
    sessions = make_session_factory(engine)
    bot = Bot(client, sessions, settings.admin_id_set, background=False)
    try:
        async with sessions() as session:
            reminders = await reminders_service.collect(session)
        sent = failed = 0
        for i, r in enumerate(reminders):
            if i:
                await asyncio.sleep(BROADCAST_INTERVAL)
            try:
                await bot._send(
                    r.student.max_user_id, reminders_service.text(r), kb.reminder_actions(r.quests)
                )
                sent += 1
            except httpx.HTTPError as exc:
                failed += 1
                log.warning("Не доставлено %s: %s", r.student.max_user_id, exc)
        log.info("Напоминания о сериях: отправлено %s, не доставлено %s", sent, failed)
    finally:
        await client.close()
        await engine.dispose()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    asyncio.run(run())


if __name__ == "__main__":
    main()
