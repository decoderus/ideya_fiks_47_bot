"""Демо-режим для жюри и обезличивание базы."""
import sqlite3

from sqlalchemy import select

from app import demo
from app.db.models import CoinTransaction, Role, SchoolClass, User
from app.db.session import init_db, make_engine
from deploy import anonymize
from tests.test_bot import (  # noqa: F401  env — фикстура
    ADMIN,
    TEST_CLASS,
    buttons,
    cb,
    env,
    get_user,
    msg,
    register,
)

JURY = 555


def payloads_of(attachments) -> list[str]:
    return [b["payload"] for b in buttons(attachments)]


async def test_new_user_enters_as_admin(env):
    bot, client, sessions = env
    bot.demo = True
    update = msg(JURY, "/start", name="Мария")
    update["message"]["sender"]["last_name"] = "Петрова"
    await bot.handle_update(update)
    _, text, att = client.sent[-1]
    assert "Привет, Мария Петрова!" in text  # имя и фамилия из профиля MAX
    assert "Демо-версия" in text and "копия бота @demo_active_bot" in text
    assert "Ваша роль: админ" in text and text.count("/demo") == 1  # подсказка — один раз, в меню
    assert "menu:school" in payloads_of(att)  # сразу меню админа, без регистрации
    user = await get_user(sessions, JURY)
    assert (user.role, user.full_name) == (Role.admin, "Мария Петрова")

    await bot.handle_update(msg(JURY, "/demo"))
    _, text, att = client.sent[-1]
    assert "Сейчас вы: админ" in text
    assert set(payloads_of(att)) == {
        "demo:role:student", "demo:role:teacher", "demo:role:moderator", "demo:role:admin"
    }


async def test_new_user_first_button_also_enters(env):
    bot, client, sessions = env
    bot.demo = True
    await bot.handle_update(cb(JURY, "menu:rating"))
    assert "Ваша роль: админ" in client.answers[-1]["text"]
    assert (await get_user(sessions, JURY)).role == Role.admin


async def test_switch_roles(env):
    bot, client, sessions = env
    async with sessions() as s:
        s.add(SchoolClass(name="5А"))
        await s.commit()
    await register(bot, 5)  # ученик в тестовом классе: он самый «населённый»
    bot.demo = True  # после регистрации: в демо-режиме новый пользователь сразу админ

    await bot.handle_update(cb(JURY, "demo:role:admin"))
    text = client.answers[-1]["text"]
    assert "Теперь вы — админ" in text and "/demo — сменить роль" in text
    user = await get_user(sessions, JURY)
    assert (user.role, user.full_name) == (Role.admin, demo.DEMO_NAME)

    await bot.handle_update(cb(JURY, "demo:role:teacher"))
    async with sessions() as s:
        user = await s.scalar(select(User).where(User.max_user_id == JURY))
        assert user.role == Role.teacher
        assert [c.name for c in user.teacher_classes][0] == TEST_CLASS

    # ученик получает стартовые монеты один раз
    for _ in range(2):
        await bot.handle_update(cb(JURY, "demo:role:student"))
    user = await get_user(sessions, JURY)
    assert (user.role, user.balance) == (Role.student, demo.START_BALANCE)
    assert user.class_id is not None
    async with sessions() as s:
        grants = list(await s.scalars(select(CoinTransaction).where(CoinTransaction.user_id == user.id)))
    assert [g.amount for g in grants] == [demo.START_BALANCE]

    await bot.handle_update(msg(JURY, "/demo"))
    assert "Сейчас вы: ученик" in client.sent[-1][1]


async def test_demo_off_ignores_demo_commands(env):
    bot, client, sessions = env
    await register(bot, ADMIN, "Админов Админ")
    await register(bot, 5)
    await bot.handle_update(cb(5, "demo:role:admin"))
    assert (await get_user(sessions, 5)).role == Role.student
    await bot.handle_update(msg(5, "/start"))
    assert "/demo" not in client.sent[-1][1]


async def test_fake_users_get_no_messages(env):
    bot, client, _ = env
    await bot._send(demo.FAKE_ID_BASE + 1, "Привет")
    assert client.sent == []
    await bot._send(7, "Привет")
    assert len(client.sent) == 1


def test_seed_database(tmp_path):
    source = tmp_path / "demo.db"
    source.write_bytes(b"demo")
    target = tmp_path / "data" / "aktiv.db"
    url = f"sqlite+aiosqlite:///{target}"
    assert demo.seed_database(url, str(source))
    assert target.read_bytes() == b"demo"
    # существующую базу не трогает
    target.write_bytes(b"live")
    assert not demo.seed_database(url, str(source))
    assert target.read_bytes() == b"live"
    assert not demo.seed_database(url, "")


async def test_anonymize(tmp_path):
    src = tmp_path / "live.db"
    engine = make_engine(f"sqlite+aiosqlite:///{src}")
    await init_db(engine)
    await engine.dispose()
    db = sqlite3.connect(src)
    db.execute("INSERT INTO classes (id, name) VALUES (1, '5А')")
    db.execute(
        "INSERT INTO users (id, max_user_id, name, full_name, role, class_id, balance, created_at) "
        "VALUES (1, 1111111, 'Глеб', 'Зарубин Глеб', 'admin', NULL, 0, '2026-10-19 08:00:00'),"
        " (2, 1234567, 'Настя', 'Лаптева Анастасия', 'student', 1, 300, '2026-10-19 08:00:00')"
    )
    db.execute(
        "INSERT INTO rewards (id, title, description, price, is_active, author_id, created_at, kind) "
        "VALUES (1, 'Респект', 'Уважение от Зарубина', 10, 1, 1, '2026-10-19 08:00:00', 'item')"
    )
    db.execute(
        "INSERT INTO quests (id, title, description, reward, kind, author_id, is_active, created_at, "
        "repeat, approval, channel_post) VALUES (1, 'Идея для школы', 'Опишите идею', 100, 'photo', 1, 1,"
        " '2026-10-19 08:00:00', 'none', 'approved', 1)"
    )
    db.execute(
        "INSERT INTO submissions (quest_id, user_id, status, text, photo, created_at) VALUES "
        "(1, 2, 'approved', 'Моя идея, Настя Лаптева', '{\"token\": \"t\"}', '2026-10-19 09:00:00')"
    )
    db.execute("INSERT INTO app_settings (key, value) VALUES ('channel_title', 'МБОУ СОШ №3')")
    db.execute(
        "INSERT INTO quest_questions (id, quest_id, student_id, text, created_at) "
        "VALUES (1, 1, 2, 'Это Настя Лаптева, можно сдать завтра?', '2026-10-19 10:00:00')"
    )
    db.execute(
        "INSERT INTO quest_answers (question_id, author_id, author_role, text, created_at) "
        "VALUES (1, 1, 'admin', 'Настя, да', '2026-10-19 11:00:00')"
    )
    db.commit()
    db.close()

    dst = tmp_path / "demo.db"
    stats = anonymize.anonymize(str(src), str(dst))
    assert stats["leaks"] == 0 and stats["pending_added"] == 1

    raw = dst.read_bytes()
    for secret in ("Зарубин", "Лаптева", "МБОУ"):
        assert secret.encode() not in raw, secret
    out = sqlite3.connect(dst)
    users = out.execute("SELECT max_user_id, full_name, balance FROM users ORDER BY id").fetchall()
    assert all(demo.is_fake(u[0]) for u in users)
    assert users[1][2] == 300  # балансы сохраняются
    text, photo = out.execute("SELECT text, photo FROM submissions WHERE status = 'approved'").fetchone()
    assert photo is None and text == anonymize.hidden_answer(["photo", "text"])
    assert "Здесь был ответ ученика: фото и текст" in text and "152-ФЗ" in text
    pending = out.execute("SELECT text FROM submissions WHERE status = 'pending'").fetchone()[0]
    assert pending.startswith("🧪 Пример ответа, придуман для демо-версии")
    assert out.execute("SELECT description FROM rewards").fetchone()[0] == anonymize.HIDDEN_TEXT
    assert out.execute("SELECT text FROM quest_questions").fetchone()[0] == anonymize.HIDDEN_QUESTION
    assert out.execute("SELECT text, author_role FROM quest_answers").fetchone() == (
        anonymize.HIDDEN_REPLY, "admin"
    )
    # модератор в демо проверяет ответы; названия школы нет
    assert dict(out.execute("SELECT key, value FROM app_settings")) == {"moderator_review": "1"}
    # исходный файл не изменён
    assert "Зарубин Глеб" in [n for (n,) in sqlite3.connect(src).execute("SELECT full_name FROM users")]
