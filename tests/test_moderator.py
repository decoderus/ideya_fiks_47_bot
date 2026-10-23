"""Модератор: помощник админа без власти над монетами, экономикой и ролями."""
import logging

import pytest
from sqlalchemy import select

from app.db.models import BroadcastDraft, DraftStatus, Order, Quest, Role, TeacherRequest
from app.services import audit
from tests.test_bot import (  # noqa: F401  env — фикстура
    ADMIN,
    add_class,
    award,
    buttons,
    cb,
    class_id,
    create_quest,
    env,
    get_user,
    make_teacher,
    msg,
    quest_id,
    register,
    submit_text,
)
from tests.test_shop import buy, create_reward, reward_id

MOD = 30


class Collect(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(record.getMessage())


@pytest.fixture
def journal():
    handler = Collect()
    audit.log.addHandler(handler)
    yield handler.lines
    audit.log.removeHandler(handler)


def payloads(answer) -> list[str]:
    keyboards = [a for a in answer["attachments"] or [] if a["type"] == "inline_keyboard"]
    return [b["payload"] for b in buttons(keyboards)] if keyboards else []


def last_to(client, uid) -> tuple[str, list]:
    to, text, att = next(m for m in reversed(client.sent) if m[0] == uid)
    return text, att


async def school(bot, sessions):
    """Админ, учитель 7 (Тестовый класс), ученик 5 с 300 монетами, модератор 30."""
    await make_teacher(bot, 7)
    await register(bot, 5)
    await award(sessions, 5, 300)
    await register(bot, MOD, "Модеров Максим")
    await bot.handle_update(msg(ADMIN, f"/role {MOD} moderator"))


async def test_becomes_moderator(env, journal):
    bot, client, sessions = env
    await school(bot, sessions)
    assert "теперь модератор" in last_to(client, ADMIN)[0]
    mod = await get_user(sessions, MOD)
    assert (mod.role, mod.class_id) == (Role.moderator, None)
    assert any("role.set" in line and "Модеров Максим" in line and "moderator" in line for line in journal)

    await bot.handle_update(msg(MOD, "/start"))
    text, att = last_to(client, MOD)
    buttons_ = [b for row in att[0]["payload"]["buttons"] for b in row]
    menu = [b["payload"] for b in buttons_]
    for allowed in ("menu:new_quest", "ord:list", "menu:broadcast", "menu:teachers",
                    "menu:stats", "menu:rewards"):
        assert allowed in menu
    # меню как у админа, недоступное — с замком
    assert "menu:coins" not in menu and "menu:review" not in menu
    locked_titles = [b["text"] for b in buttons_ if b["payload"] == "locked"]
    assert locked_titles == ["🔒 На проверке", "🔒 Монеты"]
    await bot.handle_update(cb(MOD, "locked"))
    assert "Недоступно модератору" in client.answers[-1]["notification"]
    # не ученик: нет в рейтинге
    await bot.handle_update(cb(5, "menu:rating"))
    assert "Модеров" not in client.answers[-1]["text"]


async def test_moderator_cannot_touch_economy_or_roles(env):
    bot, client, sessions = env
    await school(bot, sessions)
    student = await get_user(sessions, 5)
    denied = [
        "menu:coins", f"coins:give:{student.id}", "menu:review", "rw:new", "rw:price:1",
        "ach:new", "sz:add", "cls:add", f"cls:del_ok:{student.id}",
    ]
    for payload in denied:
        await bot.handle_update(cb(MOD, payload))
        answer = client.answers[-1]
        assert answer["notification"], payload
    # награды — только просмотр, кнопки управления с замком
    await create_reward(bot, price="100")
    await bot.handle_update(cb(MOD, "menu:rewards"))
    assert "Только просмотр" in client.answers[-1]["text"]
    assert "rw:new" not in payloads(client.answers[-1])
    shown = len(client.answers)
    await bot.handle_update(cb(MOD, f"rw:open:{await reward_id(sessions)}"))
    assert len(client.answers) == shown + 1  # карточка открылась, а не упала
    card = payloads(client.answers[-1])
    assert "locked" in card and not any(p.startswith(("rw:price", "rw:hide")) for p in card)
    await bot.handle_update(msg(MOD, f"/role {MOD} admin"))
    assert "только админам" in last_to(client, MOD)[0]
    assert (await get_user(sessions, MOD)).role == Role.moderator
    assert (await get_user(sessions, 5)).full_name is not None


async def test_moderator_views_and_transfers(env, journal):
    bot, client, sessions = env
    await school(bot, sessions)
    other = await add_class(sessions, "5Б")
    student = await get_user(sessions, 5)

    for payload in ("menu:stats", "enl:0", "menu:journal", f"jr:st:{student.id}",
                    f"cls:hist:{student.id}:0", "menu:achievements", "menu:seasons"):
        await bot.handle_update(cb(MOD, payload))
        assert client.answers[-1]["text"], payload
    assert "• модераторов: 1" in (await _screen(bot, client, "menu:stats"))
    # достижения и сезоны — без кнопок управления
    await bot.handle_update(cb(MOD, "menu:achievements"))
    assert "ach:new" not in payloads(client.answers[-1])
    await bot.handle_update(cb(MOD, "menu:seasons"))
    assert "sz:add" not in payloads(client.answers[-1])

    await bot.handle_update(cb(MOD, f"cls:stu:{student.id}"))
    card = payloads(client.answers[-1])
    assert f"cls:mv:{student.id}" in card and f"cls:del:{student.id}" not in card
    await bot.handle_update(cb(MOD, f"cls:mvto:{student.id}:{other}"))
    assert (await get_user(sessions, 5)).class_id == other
    assert any("student.transfer" in line and "Тестовый класс → 5Б" in line for line in journal)


async def _screen(bot, client, payload) -> str:
    await bot.handle_update(cb(MOD, payload))
    return client.answers[-1]["text"]


async def test_moderator_approves_teacher_admin_can_undo(env, journal):
    bot, client, sessions = env
    await school(bot, sessions)
    # заявка учителя
    await bot.handle_update(msg(40, "/start"))
    await bot.handle_update(cb(40, "reg:role:teacher"))
    await bot.handle_update(cb(40, "reg:next"))
    await bot.handle_update(cb(40, "reg:teacher_ok"))
    await bot.handle_update(msg(40, "Новикова Нина"))
    await bot.handle_update(cb(40, f"reg:tcl:{await class_id(sessions)}"))
    await bot.handle_update(cb(40, "reg:tcl_done"))
    async with sessions() as s:
        request = await s.scalar(select(TeacherRequest))
    assert request is not None
    assert any(to == MOD and "Заявка на роль учителя" in text for to, text, _ in client.sent)

    await bot.handle_update(cb(MOD, f"tr:ok:{request.id}"))
    assert (await get_user(sessions, 40)).role == Role.teacher
    text, att = last_to(client, ADMIN)
    assert "Модератор Модеров Максим подтвердил(а) учителя: Новикова Нина" in text
    assert "Классы: Тестовый класс" in text  # класс из заявки назначен сразу
    assert f"tr:undo:{request.id}" in [b["payload"] for row in att[0]["payload"]["buttons"] for b in row]
    # модератор может поправить классы: снимает выбранный учителем класс
    await bot.handle_update(cb(MOD, f"tc:t:{(await get_user(sessions, 40)).id}:{await class_id(sessions)}"))
    assert any("teacher.class_remove" in line for line in journal)

    # отменить может только админ
    await bot.handle_update(cb(MOD, f"tr:undo:{request.id}"))
    assert "только админ" in client.answers[-1]["notification"]
    await bot.handle_update(cb(ADMIN, f"tr:undo:{request.id}"))
    teacher = await get_user(sessions, 40)
    assert (teacher.role, teacher.full_name, teacher.teacher_classes) == (Role.student, None, [])
    assert "отменено администратором" in last_to(client, 40)[0]
    assert [l.split(" | ")[4] for l in journal if "Новикова" in l] == [
        "teacher.approve", "teacher.class_remove", "teacher.undo"
    ]


async def test_moderator_quests(env, journal, fixed_ranges):
    bot, client, sessions = env
    await school(bot, sessions)
    await bot.handle_update(cb(MOD, "menu:new_quest"))
    # награду и тип не выбирает: квест с проверкой, 50–300
    assert "Шаг 1 из 5" in client.answers[-1]["text"]
    await bot.handle_update(msg(MOD, "Плакат"))
    await bot.handle_update(msg(MOD, "Нарисуйте плакат к субботнику"))
    assert "Награда: 50–300 монет" in last_to(client, MOD)[0]
    await bot.handle_update(cb(MOD, "nq:rep:none"))
    assert "Шаг 4 из 5" in client.answers[-1]["text"]
    assert "nq:aud:all" in payloads(client.answers[-1])  # вся школа, как у админа
    await bot.handle_update(cb(MOD, "nq:aud:all"))
    client.sent.clear()
    await bot.handle_update(cb(MOD, "nq:photo:none"))
    qid = await quest_id(sessions, "Плакат")
    assert "отправлен админу на подтверждение" in client.answers[-1]["text"]
    text, att = last_to(client, ADMIN)
    assert "Модератор Модеров Максим создал(а) квест и просит опубликовать" in text
    assert "Нарисуйте плакат к субботнику" in text
    assert {f"mq:apr:{qid}", f"mq:rwk:{qid}"} <= {b["payload"] for row in att[-1]["payload"]["buttons"] for b in row}
    assert any("quest.create" in line and "«Плакат»" in line for line in journal)
    # до публикации ученики квест не видят и уведомление не получают
    assert {to for to, *_ in client.sent} == {ADMIN}
    await bot.handle_update(cb(5, "menu:quests"))
    assert "Плакат" not in str(client.answers[-1])
    # модератор сам себе не опубликует и не откроет квест в обход админа
    for payload in (f"mq:apr:{qid}", f"mq:reopen:{qid}"):
        await bot.handle_update(cb(MOD, payload))
        assert client.answers[-1]["notification"], payload
    await bot.handle_update(cb(ADMIN, f"mq:apr:{qid}"))
    assert "Квест опубликован" in client.answers[-1]["text"]
    assert any(to == 5 and "Новый квест: «Плакат»" in text for to, text, _ in client.sent)
    assert "опубликовал(а) ваш квест «Плакат»" in last_to(client, MOD)[0]
    assert any("quest.approve" in line for line in journal)
    await bot.handle_update(cb(ADMIN, f"mq:apr:{qid}"))
    assert "уже рассмотрен" in client.answers[-1]["notification"]

    # ответ ученика: модератор не проверяет; уведомление — только админу
    # (учитель класса видит заявку в «На проверке», но не получает сообщение)
    client.sent.clear()
    sub = await submit_text(bot, sessions, 5, "Нарисовал", title="Плакат")
    notified = {to for to, text, _ in client.sent if "Новая заявка" in text}
    assert notified == {ADMIN}
    await bot.handle_update(cb(7, "menu:review"))
    assert f"rev:open:{sub.id}" in str(client.answers[-1]["attachments"])
    await bot.handle_update(cb(MOD, f"rev:ok:{sub.id}"))
    assert client.answers[-1]["notification"]

    # свой квест правит, чужой — только смотрит
    await bot.handle_update(cb(MOD, f"mq:ef:reward:{qid}"))
    await bot.handle_update(msg(MOD, "250"))
    async with sessions() as s:
        assert (await s.get(Quest, qid)).reward == 250
    await create_quest(bot, 7, title="Учительский")
    other = await quest_id(sessions, "Учительский")
    await bot.handle_update(cb(MOD, f"mq:open:{other}"))
    assert "только просмотр" in client.answers[-1]["text"]
    await bot.handle_update(cb(MOD, f"mq:close_ok:{other}"))
    assert "не найден" in client.answers[-1]["notification"]


async def test_admin_returns_moderator_quest_for_rework(env, journal):
    bot, client, sessions = env
    await school(bot, sessions)
    await create_quest(bot, MOD, title="Сырой квест")
    qid = await quest_id(sessions, "Сырой квест")

    await bot.handle_update(cb(ADMIN, f"mq:rwk:{qid}"))
    assert f"mq:rwk0:{qid}" in payloads(client.answers[-1])
    await bot.handle_update(msg(ADMIN, "Добавьте описание задания"))
    text = last_to(client, MOD)[0]
    assert "на доработку" in text and "Комментарий: Добавьте описание задания" in text
    async with sessions() as s:
        quest = await s.get(Quest, qid)
        assert (quest.approval, quest.is_active) == ("rework", False)

    # модератор видит комментарий, правит и отправляет снова
    await bot.handle_update(cb(MOD, f"mq:open:{qid}"))
    assert "Комментарий админа: Добавьте описание задания" in client.answers[-1]["text"]
    assert f"mq:resub:{qid}" in payloads(client.answers[-1])
    await bot.handle_update(cb(MOD, f"mq:ef:description:{qid}"))
    await bot.handle_update(msg(MOD, "Подробное описание"))
    await bot.handle_update(cb(MOD, f"mq:resub:{qid}"))
    assert "снова отправлен" in client.answers[-1]["text"]
    assert "доработал(а) квест" in last_to(client, ADMIN)[0]

    # возврат без комментария, затем публикация после второй доработки
    await bot.handle_update(cb(ADMIN, f"mq:rwk:{qid}"))
    await bot.handle_update(cb(ADMIN, f"mq:rwk0:{qid}"))
    assert "Комментарий" not in last_to(client, MOD)[0]
    await bot.handle_update(cb(MOD, f"mq:resub:{qid}"))
    await bot.handle_update(cb(ADMIN, f"mq:apr:{qid}"))
    async with sessions() as s:
        quest = await s.get(Quest, qid)
        assert (quest.approval, quest.approval_note, quest.is_active) == (None, None, True)
    assert [l.split(" | ")[4] for l in journal if "Сырой квест" in l] == [
        "quest.create", "quest.rework", "quest.edit", "quest.resubmit",
        "quest.rework", "quest.resubmit", "quest.approve",
    ]


async def test_moderator_issues_but_cannot_cancel_orders(env, journal):
    bot, client, sessions = env
    await school(bot, sessions)
    await create_reward(bot, price="100")
    await buy(bot, 5, await reward_id(sessions))
    async with sessions() as s:
        order = await s.scalar(select(Order))
    await bot.handle_update(cb(MOD, f"ord:open:{order.id}"))
    actions = payloads(client.answers[-1])
    assert f"ord:issue:{order.id}" in actions and f"ord:cancel:{order.id}" not in actions
    for payload in (f"ord:cancel:{order.id}", f"ord:cancel_ok:{order.id}"):
        await bot.handle_update(cb(MOD, payload))
        assert "учитель класса и админ" in client.answers[-1]["notification"]
    await bot.handle_update(cb(MOD, f"ord:issue:{order.id}"))
    assert "выдано" in client.answers[-1]["text"]
    assert any("order.issue" in line for line in journal)

    # в магазине — только просмотр
    await bot.handle_update(cb(MOD, f"shop:open:{await reward_id(sessions)}"))
    assert not any(p.startswith("shop:buy:") for p in payloads(client.answers[-1]))


async def test_moderator_broadcast_needs_admin(env, journal):
    bot, client, sessions = env
    await school(bot, sessions)
    await bot.handle_update(cb(MOD, "bc:new"))
    await bot.handle_update(msg(MOD, "Завтра субботник!"))
    await bot.handle_update(cb(MOD, "bc:aud:all"))
    assert "после подтверждения администратора" in client.answers[-1]["text"]
    client.sent.clear()
    await bot.handle_update(cb(MOD, "bc:send"))
    assert "на подтверждение" in client.answers[-1]["text"]
    assert {to for to, *_ in client.sent} == {ADMIN}  # ученикам ничего не ушло
    async with sessions() as s:
        draft = await s.scalar(select(BroadcastDraft))
    text, att = last_to(client, ADMIN)
    assert "Завтра субботник!" in text
    assert f"bcd:ok:{draft.id}" in [b["payload"] for row in att[-1]["payload"]["buttons"] for b in row]

    # модератор сам себе не подтвердит
    await bot.handle_update(cb(MOD, f"bcd:ok:{draft.id}"))
    assert "только админ" in client.answers[-1]["notification"]

    await bot.handle_update(cb(ADMIN, f"bcd:ok:{draft.id}"))
    assert any(to == 5 and "Завтра субботник!" in text for to, text, _ in client.sent)
    assert "подтвердил ваше оповещение" in last_to(client, MOD)[0]
    async with sessions() as s:
        assert (await s.get(BroadcastDraft, draft.id)).status == DraftStatus.sent
    await bot.handle_update(cb(ADMIN, f"bcd:no:{draft.id}"))
    assert "уже отправлено" in client.answers[-1]["notification"]
    actions = [l.split(" | ")[4] for l in journal if "broadcast." in l]
    assert actions == ["broadcast.draft", "broadcast.approve"]


async def test_admin_rejects_moderator_broadcast(env):
    bot, client, sessions = env
    await school(bot, sessions)
    await bot.handle_update(cb(MOD, "bc:new"))
    await bot.handle_update(msg(MOD, "Спорное объявление"))
    await bot.handle_update(cb(MOD, "bc:aud:all"))
    await bot.handle_update(cb(MOD, "bc:send"))
    async with sessions() as s:
        draft = await s.scalar(select(BroadcastDraft))
    client.sent.clear()
    await bot.handle_update(cb(ADMIN, f"bcd:no:{draft.id}"))
    assert {to for to, *_ in client.sent} == {MOD}
    assert "не стал отправлять" in last_to(client, MOD)[0]


def test_audit_line_format(tmp_path):
    from app.db.models import User

    path = tmp_path / "audit.log"
    audit.setup(str(path))
    try:
        actor = User(id=3, max_user_id=777, name="M", full_name="Модеров Максим", role=Role.moderator)
        audit.record(actor, "teacher.approve", "Новикова Нина (заявка №1)")
        line = path.read_text(encoding="utf-8").strip()
        parts = line.split(" | ")
        assert parts[0].endswith("МСК")
        assert parts[1:] == [
            "Модеров Максим", "модератор", "id 3, max 777", "teacher.approve",
            "Новикова Нина (заявка №1)",
        ]
    finally:
        for h in list(audit.log.handlers):
            if isinstance(h, logging.handlers.RotatingFileHandler):
                audit.log.removeHandler(h)
                h.close()


async def test_admin_lets_moderators_review_temporarily(env, journal):
    bot, client, sessions = env
    await school(bot, sessions)
    await create_quest(bot, ADMIN, title="Плакат", kind="photo")
    sub = await submit_text(bot, sessions, 5, "Готово", title="Плакат")

    # по умолчанию выключено
    await bot.handle_update(cb(MOD, f"rev:ok:{sub.id}"))
    assert client.answers[-1]["notification"]
    await bot.handle_update(msg(MOD, "/modreview on"))
    assert "только админам" in last_to(client, MOD)[0]

    await bot.handle_update(msg(ADMIN, "/modreview"))
    assert "сейчас выключена" in last_to(client, ADMIN)[0]
    await bot.handle_update(msg(ADMIN, "/modreview on"))
    assert "Модераторы теперь проверяют ответы" in last_to(client, ADMIN)[0]
    assert "разрешил модераторам проверять" in last_to(client, MOD)[0]
    assert any("settings.moderator_review | включена" in line for line in journal)

    await bot.handle_update(msg(MOD, "/start"))
    menu = [b for row in last_to(client, MOD)[1][0]["payload"]["buttons"] for b in row]
    review = [b["text"] for b in menu if b["payload"] == "menu:review"]
    assert review == ["📋 На проверке"]
    assert [b["text"] for b in menu if b["payload"] == "locked"] == ["🔒 Монеты"]

    await bot.handle_update(cb(MOD, "menu:review"))
    assert f"rev:open:{sub.id}" in payloads(client.answers[-1])

    client.sent.clear()
    await bot.handle_update(cb(MOD, f"rev:ok:{sub.id}"))
    assert (await get_user(sessions, 5)).balance == 350
    text, att = last_to(client, ADMIN)
    assert "Модератор Модеров Максим проверил(а) заявку" in text
    assert f"rev:flip:{sub.id}" in [b["payload"] for row in att[-1]["payload"]["buttons"] for b in row]
    assert any("submission.approve" in line for line in journal)

    # админ меняет решение — модератор узнаёт
    await bot.handle_update(cb(ADMIN, f"rev:flip:{sub.id}"))
    assert "изменил(а) ваше решение" in last_to(client, MOD)[0]
    assert (await get_user(sessions, 5)).balance == 300

    await bot.handle_update(msg(ADMIN, "/modreview off"))
    assert "закрыт" in last_to(client, MOD)[0]
    await bot.handle_update(cb(MOD, "menu:review"))
    assert client.answers[-1]["notification"]


async def test_new_answer_notifies_moderators_when_enabled(env):
    bot, client, sessions = env
    await school(bot, sessions)
    await bot.handle_update(msg(ADMIN, "/modreview on"))
    await create_quest(bot, 7, title="Отчёт", kind="photo")
    client.sent.clear()
    await submit_text(bot, sessions, 5, "Сделал", title="Отчёт")
    notified = {to for to, text, _ in client.sent if "Новая заявка" in text}
    assert notified == {7, ADMIN, MOD}


async def test_reviewing_moderator_sees_all_archives(env, journal):
    """Пока модераторы проверяют ответы, им видны текущие заявки, общий архив
    и раздел засчитанных ответов ежедневных квестов."""
    bot, client, sessions = env
    await school(bot, sessions)
    await create_quest(bot, ADMIN, title="Зарядка", kind="photo", repeat="daily")
    await create_quest(bot, ADMIN, title="Сочинение", kind="photo")
    daily = await submit_text(bot, sessions, 5, "Сделал", title="Зарядка")
    await bot.handle_update(cb(ADMIN, f"rev:ok:{daily.id}"))
    pending = await submit_text(bot, sessions, 5, "Текст", title="Сочинение")

    def seen():
        return [b["payload"] for b in buttons(client.answers[-1]["attachments"])]

    # проверка модераторами выключена — раздела нет
    for payload in ("arch:page:0", "arch:daily:0"):
        await bot.handle_update(cb(MOD, payload))
        assert "Раздел доступен" in client.answers[-1]["notification"]

    await bot.handle_update(msg(ADMIN, "/modreview on"))
    await bot.handle_update(cb(MOD, "menu:review"))
    assert f"rev:open:{pending.id}" in seen()
    await bot.handle_update(cb(MOD, "arch:page:0"))
    assert "arch:daily:0" in seen()
    await bot.handle_update(cb(MOD, "arch:daily:0"))
    assert f"arch:open:{daily.id}" in seen()
    await bot.handle_update(cb(MOD, f"arch:open:{daily.id}"))
    assert client.answers[-1]["notification"] != "Заявка не найдена."
