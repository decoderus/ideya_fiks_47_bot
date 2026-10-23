"""Запуск бота (long polling): python -m app.main"""
import asyncio
import logging

import httpx

from app import demo, scheduler
from app.bot import Bot
from app.config import get_settings
from app.db.migrate import migrate
from app.db.session import make_engine, make_session_factory
from app.max_api import MaxClient
from app.services import audit

log = logging.getLogger("aktiv")


async def run() -> None:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    client = MaxClient(settings.bot_token, settings.max_api_base, settings.ca_bundle)
    bot = Bot(client, make_session_factory(engine), settings.admin_id_set, demo=settings.demo)

    me = await client.get_me()
    log.info("Бот запущен: @%s (id %s)", me.get("username"), me.get("user_id"))

    # Ссылку держим, чтобы задачу не собрал сборщик мусора
    jobs = asyncio.create_task(scheduler.run()) if settings.scheduler else None
    if jobs:
        log.info("Плановые задачи: расписание запущено")

    marker: int | None = None
    try:
        while True:
            try:
                data = await client.get_updates(marker)
            except (httpx.HTTPError, RuntimeError) as exc:
                log.warning("Ошибка опроса: %s. Повтор через 5 с", exc)
                await asyncio.sleep(5)
                continue
            marker = data.get("marker", marker)
            for update in data.get("updates", []):
                await bot.handle_update(update)
    finally:
        if jobs:
            jobs.cancel()
        await bot.wait_background()
        await client.close()
        await engine.dispose()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    # httpx пишет строку на каждый запрос, в том числе на каждый опрос MAX — это шум.
    # Ошибки запросов бот логирует сам.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    audit.setup(get_settings().audit_log)
    if demo.seed_database(get_settings().database_url, get_settings().demo_db):
        log.info("Демо-база скопирована из %s", get_settings().demo_db)
    # Миграции до запуска цикла событий: Alembic сам вызывает asyncio.run
    migrate(get_settings().database_url)
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
