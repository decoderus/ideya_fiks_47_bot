"""Запуск бота (long polling): python -m app.main"""
import asyncio
import logging

import httpx

from app import demo, scheduler
from app import health
from app.bot import Bot
from app.config import get_settings
from app.db.migrate import migrate
from app.db.session import make_engine, make_session_factory
from app.max_api import MaxClient
from app.services import audit

log = logging.getLogger("aktiv")


async def start_web(bot: Bot, sessions, settings):
    """Веб-интерфейс в том же процессе: страница, API и пропуски для кнопки «🌐 Веб-версия»."""
    from app.web import auth as web_auth
    from app.web import server as web_server

    secret = web_auth.secret_from(settings.bot_token, settings.web_secret)
    bot.web_tickets = web_auth.Tickets(secret)
    bot.web_url = settings.web_url or f"http://localhost:{settings.web_port}"
    app = web_server.make_app(sessions, settings.bot_token, secret, bot.web_tickets)
    return await web_server.start(app, settings.web_host, settings.web_port)


async def run() -> None:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    client = MaxClient(settings.bot_token, settings.max_api_base, settings.ca_bundle)
    sessions = make_session_factory(engine)
    if settings.demo:
        async with sessions() as session:
            await demo.check_database(session)
    bot = Bot(client, sessions, settings.admin_id_set, demo=settings.demo)
    web_runner = await start_web(bot, sessions, settings) if settings.web_port else None

    heartbeat = health.Heartbeat(settings.heartbeat_file)
    previous = heartbeat.read()  # как закончился прошлый запуск
    me = await client.get_me()
    log.info("Бот запущен: @%s (id %s)", me.get("username"), me.get("user_id"))
    heartbeat.beat()
    bot.alert_ids = settings.alert_id_set
    notice = health.restart_notice(previous)
    if notice:
        log.warning("Прошлый запуск закончился сбоем, оповещаем админов")
        await bot.alert_admins(notice)

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
                bot.watchdog.poll_failed()
                await asyncio.sleep(5)
                continue
            heartbeat.beat()
            notice = bot.watchdog.poll_ok()
            if notice:
                await bot.alert_admins(notice)
            marker = data.get("marker", marker)
            for update in data.get("updates", []):
                await bot.handle_update(update)
    finally:
        if jobs:
            jobs.cancel()
        if web_runner:
            await web_runner.cleanup()
        await bot.wait_background()
        await client.close()
        await engine.dispose()
        heartbeat.stopped()  # штатная остановка — при следующем запуске не «сбой»


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
