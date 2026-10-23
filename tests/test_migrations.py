"""Миграции: схема после них совпадает с моделями, старая база не теряет данные."""
import sqlite3

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

from app.db.migrate import migrate
from app.db.models import Base

def schema_diff(path) -> list:
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.connect() as conn:
            return compare_metadata(MigrationContext.configure(conn), Base.metadata)
    finally:
        engine.dispose()


def version(path) -> str:
    with sqlite3.connect(path) as conn:
        return conn.execute("select version_num from alembic_version").fetchone()[0]


def test_fresh_database_matches_models(tmp_path):
    """Если тест падает, модели изменили, а миграцию не создали:
    alembic revision --autogenerate -m "..."
    """
    path = tmp_path / "fresh.db"
    migrate(f"sqlite+aiosqlite:///{path}")
    assert schema_diff(path) == []


def test_migrate_is_idempotent(tmp_path):
    path = tmp_path / "twice.db"
    migrate(f"sqlite+aiosqlite:///{path}")
    migrate(f"sqlite+aiosqlite:///{path}")
    assert schema_diff(path) == []


def test_legacy_database_is_stamped_and_keeps_data(tmp_path):
    """База, созданная через create_all до появления миграций."""
    path = tmp_path / "legacy.db"
    url = f"sqlite+aiosqlite:///{path}"

    # Схема ревизии 0001 без отметки версии — так выглядела база,
    # созданная через create_all до появления миграций
    from alembic import command

    from app.db.migrate import alembic_config

    command.upgrade(alembic_config(url), "0001")
    with sqlite3.connect(path) as conn:
        conn.execute("drop table alembic_version")
    with sqlite3.connect(path) as conn:
        conn.execute(
            "insert into users (max_user_id, name, role, balance, created_at) "
            "values (1, 'Аня', 'student', 70, '2026-10-18')"
        )

    migrate(url)

    assert version(path) is not None
    assert schema_diff(path) == []
    with sqlite3.connect(path) as conn:
        assert conn.execute("select name, balance from users").fetchall() == [("Аня", 70)]


def test_wheel_of_fortune_converted(tmp_path):
    """Миграция 0003 превращает «Колесо Фортуны» в колесо: 5% выиграть 10 000."""
    from alembic import command

    from app.db.migrate import alembic_config

    path = tmp_path / "wheel.db"
    url = f"sqlite+aiosqlite:///{path}"
    cfg = alembic_config(url)
    command.upgrade(cfg, "0002")
    with sqlite3.connect(path) as conn:
        conn.execute("insert into users (id, max_user_id, name, role, balance, created_at) "
                     "values (1, 1, 'Админ', 'admin', 0, '2026-10-18')")
        for title, stock in (("Колесо Фортуны", 1), ("Набор стикеров", 5)):
            conn.execute(
                "insert into rewards (title, description, price, stock, is_active, author_id, "
                "created_at) values (?, '', 200, ?, 1, 1, '2026-10-18')",
                (title, stock),
            )
    command.upgrade(cfg, "head")
    with sqlite3.connect(path) as conn:
        rows = conn.execute(
            "select title, kind, win_chance, prize, stock from rewards order by id"
        ).fetchall()
    assert rows == [
        ("Колесо Фортуны", "lottery", 5, 10000, None),
        ("Набор стикеров", "item", None, None, 5),
    ]
