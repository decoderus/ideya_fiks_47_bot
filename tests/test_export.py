"""Выгрузка в таблицу LibreOffice (.ods): права, листы, содержимое, корректный файл."""
import io
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db.models import CoinTransaction, Submission, SubmissionStatus, Success, User
from app.services import export as export_service
from app.services import ods
from tests.test_bot import (  # noqa: F401  env — фикстура
    ADMIN,
    buttons,
    cb,
    class_id,
    env,
    get_user,
    make_teacher,
    msg,
    register,
)
from tests.test_pulse import quest

T = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
TXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"


def read_ods(data: bytes) -> dict[str, list[list[str]]]:
    """Листы .ods как таблицы строк."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert z.namelist()[0] == "mimetype" and z.read("mimetype") == ods.MIME.encode()
        root = ET.fromstring(z.read("content.xml"))
        ET.fromstring(z.read("settings.xml"))
    sheets = {}
    for table in root.iter(f"{T}table"):
        rows = []
        for row in table.iter(f"{T}table-row"):
            rows.append(["\n".join(p.text or "" for p in c.iter(f"{TXT}p")) for c in row.iter(f"{T}table-cell")])
        sheets[table.get(f"{T}name")] = rows
    return sheets


async def school_with_data(bot, sessions):
    await make_teacher(bot, 7)
    await register(bot, 5, "Иванов Иван")
    await register(bot, 6, "Петрова Мария")
    task = await quest(sessions, "Порядок на столе")
    now = datetime.now(timezone.utc)
    async with sessions() as s:
        ivanov = await s.scalar(select(User).where(User.max_user_id == 5))
        teacher = await s.scalar(select(User).where(User.max_user_id == 7))
        s.add(Submission(quest_id=task, user_id=ivanov.id, status=SubmissionStatus.approved,
                         reward=80, created_at=now - timedelta(days=2), reviewed_at=now - timedelta(days=1)))
        s.add(Submission(quest_id=task, user_id=ivanov.id, status=SubmissionStatus.approved,
                         reward=50, created_at=now - timedelta(days=40), reviewed_at=now - timedelta(days=40)))
        s.add(CoinTransaction(user_id=ivanov.id, actor_id=teacher.id, amount=30, reason="Помог с уборкой"))
        s.add(Success(student_id=ivanov.id, author_id=teacher.id, author_role=teacher.role,
                      category="sport", text="Победил на «Весёлых стартах» & <финал>", coins=0))
        await s.commit()


async def test_export_content(env):
    bot, client, sessions = env
    await school_with_data(bot, sessions)
    cid = await class_id(sessions)
    async with sessions() as s:
        week = await export_service.build(s, cid, 7)
        whole = await export_service.build(s, 0, 0)
    sheets = read_ods(week.data)
    assert list(sheets) == ["Сводка", "Успехи", "Монеты"]
    summary = {row[0]: row for row in sheets["Сводка"][1:]}
    assert summary["Иванов Иван"][3:7] == ["1", "80", "30", "0"]  # 1 квест за неделю, 80 монет, +30 вручную
    assert summary["Петрова Мария"][3] == "0"
    assert sheets["Успехи"][1][4] == "Победил на «Весёлых стартах» & <финал>"  # спецсимволы не ломают XML
    assert sheets["Успехи"][1][3] == export_service.BY_KEY["sport"].title
    sources = [row[4] for row in sheets["Монеты"][1:]]
    assert "Квест «Порядок на столе»" in sources and "Помог с уборкой" in sources
    assert week.movements == 2 and whole.movements == 3  # старый квест — только за всё время
    assert "Тестовый" in week.filename and week.filename.endswith(".ods")


async def test_export_rights_and_sending(env):
    bot, client, sessions = env
    await school_with_data(bot, sessions)
    cid = await class_id(sessions)

    # учитель своего класса: кнопка в пульсе, выбор периода, файл приходит сообщением
    await bot.handle_update(cb(7, f"jr:cls:{cid}:0"))
    assert f"jr:xls:{cid}" in [b["payload"] for b in buttons(client.answers[-1]["attachments"])]
    await bot.handle_update(cb(7, f"jr:xls:{cid}"))
    assert f"jr:xp:{cid}:30" in [b["payload"] for b in buttons(client.answers[-1]["attachments"])]
    await bot.handle_update(cb(7, f"jr:xp:{cid}:30"))
    assert "Таблица отправлена" in client.answers[-1]["text"]
    to, caption, attachments = client.sent[-1]
    assert to == 7 and attachments == [{"type": "file", "payload": {"token": "file.1"}}]
    assert "Учеников: 2" in caption
    filename, data, mime = client.files[-1]
    assert mime == ods.MIME and read_ods(data)["Сводка"][1][0] == "Иванов Иван"

    # учитель — не всю школу; модератор не выгружает; админ — всю школу из статистики
    await bot.handle_update(cb(7, "jr:xls:0"))
    assert "администратор" in client.answers[-1]["notification"]
    await register(bot, 30, "Модеров Максим")
    await bot.handle_update(msg(ADMIN, "/role 30 moderator"))
    await bot.handle_update(cb(30, f"jr:xls:{cid}"))
    assert "администратор" in client.answers[-1]["notification"]
    await bot.handle_update(cb(ADMIN, "menu:stats"))
    assert "jr:xls:0" in [b["payload"] for b in buttons(client.answers[-1]["attachments"])]
    await bot.handle_update(cb(ADMIN, "jr:xp:0:0"))
    assert "Таблица отправлена" in client.answers[-1]["text"]
    assert client.sent[-1][0] == ADMIN and "вся_школа" in client.files[-1][0]


def test_ods_types():
    sheet = ods.Sheet("Лист", ["A", "B", "C"], [2, 2, 2])
    sheet.rows.append([5, datetime(2026, 10, 26, 8, 30), "две\nстроки"])
    content = read_ods(ods.build([sheet]))["Лист"]
    assert content[1] == ["5", "26.10.2026 08:30", "две\nстроки"]
