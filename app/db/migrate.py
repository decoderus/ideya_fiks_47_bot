"""Применение миграций Alembic при запуске бота."""
import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

ROOT = Path(__file__).resolve().parents[2]

# Первая ревизия совпадает со схемой, которую раньше создавал create_all
INITIAL_REVISION = "0001"


def alembic_config(url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.attributes["url"] = url
    cfg.attributes["configure_logger"] = False
    return cfg


async def _table_names(url: str) -> set[str]:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            return set(await conn.run_sync(lambda c: inspect(c).get_table_names()))
    finally:
        await engine.dispose()


def migrate(url: str) -> None:
    """Доводит базу до последней версии схемы.

    Вызывается до запуска цикла событий: Alembic сам запускает asyncio.run.
    """
    cfg = alembic_config(url)
    tables = asyncio.run(_table_names(url))
    if "users" in tables and "alembic_version" not in tables:
        # База создана до появления миграций: помечаем, что первая ревизия уже применена
        command.stamp(cfg, INITIAL_REVISION)
    command.upgrade(cfg, "head")
