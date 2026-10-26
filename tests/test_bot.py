from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.bot import Bot
from app.db.models import (
    Broadcast,
    BroadcastDraft,
    CoinTransaction,
    Order,
    Quest,
    QuestKind,
    QuestRepeat,
    Reward,
    Role,
    SchoolClass,
    Submission,
    SubmissionStatus,
    User,
)
from app.db.session import init_db, make_engine, make_session_factory
from app.services import quests as quests_service

ADMIN = 100
TEST_CLASS = "Тестовый класс"


class FakeClient:
    def __init__(self):
        self.sent = []
        self.answers = []
        self.deleted = []
        self.fail_delete = set()
        self.chat_posts = []
        self.files = []

    async def send_message(self, user_id, text, attachments=None):
        self.sent.append((user_id, text, attachments))
        return {"message": {"body": {"mid": f"mid.{len(self.sent)}"}}}

    async def send_to_chat(self, chat_id, text, attachments=None):
        self.chat_posts.append((chat_id, text, attachments))
        return {"message": {"body": {"mid": f"post.{len(self.chat_posts)}"}}}

    async def upload_file(self, filename, data, mime):
        self.files.append((filename, data, mime))
        return {"token": f"file.{len(self.files)}"}

    async def get_chat(self, chat_id):
        return {"chat_id": chat_id, "title": "Школьные новости"}

    async def delete_message(self, mid):
        if mid in self.fail_delete:
            import httpx

            raise httpx.HTTPStatusError(
                "gone", request=httpx.Request("DELETE", "x"), response=httpx.Response(404)
            )
        self.deleted.append(mid)
        return {"success": True}

    async def answer_callback(self, callback_id, text=None, attachments=None, notification=None):
        self.answers.append(
            {"id": callback_id, "text": text, "attachments": attachments, "notification": notification}
        )


@pytest.fixture
async def env():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    sessions = make_session_factory(engine)
    async with sessions() as s:
        s.add(SchoolClass(name=TEST_CLASS))  # класс, в который регистрируются ученики
        await s.commit()
    client = FakeClient()
    bot = Bot(client, sessions, admin_ids={ADMIN}, background=False, auto_achievements=False)
    yield bot, client, sessions
    await engine.dispose()


def msg(user_id, text, name="Имя", attachments=None):
    body = {"mid": "m", "seq": 1, "text": text}
    if attachments:
        body["attachments"] = attachments
    return {
        "update_type": "message_created",
        "message": {
            "sender": {"user_id": user_id, "first_name": name, "is_bot": False},
            "recipient": {"chat_type": "dialog", "user_id": 1},
            "body": body,
        },
    }


def cb(user_id, payload):
    return {
        "update_type": "message_callback",
        "callback": {"callback_id": f"cb-{payload}", "payload": payload, "user": {"user_id": user_id}},
    }


PHOTO = [{"type": "image", "payload": {"photo_id": 1, "token": "tok", "url": "https://x/1.jpg"}}]


def buttons(attachments):
    return [b for row in attachments[0]["payload"]["buttons"] for b in row]


async def get_user(sessions, uid) -> User:
    async with sessions() as s:
        return await s.scalar(select(User).where(User.max_user_id == uid))


async def class_id(sessions, name=TEST_CLASS) -> int:
    async with sessions() as s:
        return (await s.scalar(select(SchoolClass).where(SchoolClass.name == name))).id


async def register(bot, uid, full_name="Иванов Иван", school_class=TEST_CLASS):
    """Админ вводит только ФИО; ученик: роль по умолчанию → ФИО → класс."""
    await bot.handle_update(msg(uid, "/start"))
    if uid != ADMIN:
        await bot.handle_update(cb(uid, "reg:next"))
    await bot.handle_update(msg(uid, full_name))
    if uid != ADMIN:
        cid = await class_id(bot.sessions, school_class)
        await bot.handle_update(cb(uid, f"reg:class:{cid}"))


async def assign_class(bot, uid, school_class=TEST_CLASS):
    """Админ назначает учителю класс."""
    teacher = await get_user(bot.sessions, uid)
    cid = await class_id(bot.sessions, school_class)
    await bot.handle_update(cb(ADMIN, f"tc:t:{teacher.id}:{cid}"))


async def make_teacher(bot, uid, name="Учителева Анна", school_class=TEST_CLASS):
    await register(bot, ADMIN, "Админов Админ")
    await register(bot, uid, name)
    await bot.handle_update(msg(ADMIN, f"/role {uid} teacher"))
    await assign_class(bot, uid, school_class)


async def create_quest(
    bot,
    uid,
    title="Убрать класс",
    reward="50",
    kind="auto",
    audience="all",
    repeat="none",
    photo=False,
):
    await bot.handle_update(cb(uid, "menu:new_quest"))
    await bot.handle_update(msg(uid, title))
    await bot.handle_update(msg(uid, "Описание"))
    await bot.handle_update(msg(uid, reward))
    await bot.handle_update(cb(uid, f"nq:kind:{kind}"))
    await bot.handle_update(cb(uid, f"nq:rep:{repeat}"))
    if uid in (ADMIN, 30):  # админ и модератор (30) выбирают, для кого квест
        await bot.handle_update(cb(uid, f"nq:aud:{audience}"))
    if photo:
        await bot.handle_update(msg(uid, "", attachments=PHOTO))
    else:
        await bot.handle_update(cb(uid, "nq:photo:none"))


async def quest_id(sessions, title="Убрать класс"):
    async with sessions() as s:
        return (await s.scalar(select(Quest).where(Quest.title == title))).id


# --- регистрация и роли ---


async def test_new_user_chooses_role(env):
    bot, client, sessions = env
    await bot.handle_update(msg(5, "/start"))
    _, text, att = client.sent[-1]
    assert "Кто вы?" in text
    texts = [b["text"] for b in buttons(att)]
    assert "✅ 🎒 Ученик" in texts and "👩‍🏫 Учитель" in texts  # ученик по умолчанию
    assert (await get_user(sessions, 5)).full_name is None


async def test_registration_adds_to_test_class(env):
    bot, client, sessions = env
    await register(bot, 5, "петров-водкин  кузьма")
    user = await get_user(sessions, 5)
    assert user.full_name == "Петров-Водкин Кузьма"
    assert user.school_class.name == "Тестовый класс"
    assert user.role == Role.student
    answer = client.answers[-1]
    assert "Вы зачислены в класс Тестовый класс" in answer["text"]
    assert "Ваша роль: ученик" in answer["text"]
    assert answer["attachments"][0]["type"] == "inline_keyboard"


async def test_bad_name_rejected(env):
    bot, client, sessions = env
    await bot.handle_update(msg(5, "/start"))
    await bot.handle_update(cb(5, "reg:next"))
    for bad in ["Иван", "Иван123 Петров", "a b c d"]:
        await bot.handle_update(msg(5, bad))
        assert "Не получилось" in client.sent[-1][1]
    assert (await get_user(sessions, 5)).full_name is None


async def test_unregistered_callback_shows_registration(env):
    bot, client, _ = env
    await bot.handle_update(cb(5, "menu:quests"))
    assert "Кто вы?" in client.answers[-1]["text"]


async def test_admin_from_env(env):
    bot, _, sessions = env
    await register(bot, ADMIN)
    assert (await get_user(sessions, ADMIN)).role == Role.admin


async def test_bot_started_event(env):
    bot, client, sessions = env
    await bot.handle_update(
        {"update_type": "bot_started", "chat_id": 1, "user": {"user_id": 7, "first_name": "Аня"}}
    )
    assert await get_user(sessions, 7) is not None
    assert "Кто вы?" in client.sent[0][1]


async def test_id_command(env):
    bot, client, _ = env
    await bot.handle_update(msg(5, "/id"))
    assert "5" in client.sent[0][1]


async def test_role_command_admin_only(env):
    bot, client, sessions = env
    await register(bot, 5)
    await register(bot, ADMIN, "Админов Админ")
    await bot.handle_update(msg(5, "/role 5 admin"))
    assert (await get_user(sessions, 5)).role == Role.student
    assert "только админам" in client.sent[-1][1]

    await bot.handle_update(msg(ADMIN, "/role 5 teacher"))
    assert (await get_user(sessions, 5)).role == Role.teacher

    await bot.handle_update(msg(ADMIN, "/role 999 teacher"))
    assert "не найден" in client.sent[-1][1]

    await bot.handle_update(msg(ADMIN, "/role abc"))
    assert "Формат" in client.sent[-1][1]


async def test_balance_history(env):
    bot, client, sessions = env
    await make_teacher(bot, 7)
    await create_quest(bot, 7)
    await register(bot, 5)
    await bot.handle_update(cb(5, f"q:done:{await quest_id(sessions)}"))
    student = await get_user(sessions, 5)
    async with sessions() as s:
        reward = Reward(title="DJ на перемене", price=30, author_id=student.id)
        s.add(reward)
        await s.flush()
        s.add(Order(reward_id=reward.id, user_id=student.id, price=30))
        (await s.get(User, student.id)).balance -= 30
        await s.commit()

    # учитель: Мои классы → класс → ученики → ученик → история
    await bot.handle_update(cb(7, "menu:classes"))
    cid = await class_id(sessions)
    assert f"cls:open:{cid}" in payloads_of(client.answers[-1])
    assert "cls:add" not in payloads_of(client.answers[-1])
    await bot.handle_update(cb(7, f"cls:open:{cid}"))
    assert "cls:rm:" not in " ".join(payloads_of(client.answers[-1]))
    await bot.handle_update(cb(7, f"cls:st:{cid}:0"))
    assert f"cls:stu:{student.id}" in payloads_of(client.answers[-1])
    await bot.handle_update(cb(7, f"cls:stu:{student.id}"))
    card = payloads_of(client.answers[-1])
    assert f"cls:hist:{student.id}:0" in card and f"cls:del:{student.id}" not in card

    await bot.handle_update(cb(7, f"cls:hist:{student.id}:0"))
    text = client.answers[-1]["text"]
    assert "Сейчас: 20 монет" in text
    lines = text.splitlines()
    assert any("🎁 «DJ на перемене» −30 → 💰20" in line for line in lines)
    assert any("✅ «Убрать класс» +50 → 💰50" in line for line in lines)

    # управлять классом учитель не может
    await bot.handle_update(cb(7, f"cls:del_ok:{student.id}"))
    assert "только админам" in client.answers[-1]["notification"]
    # у админа в карточке ученика тоже есть история
    await bot.handle_update(cb(ADMIN, f"cls:stu:{student.id}"))
    assert f"cls:hist:{student.id}:0" in payloads_of(client.answers[-1])


async def test_teacher_classes_only_own(env):
    bot, client, sessions = env
    await make_teacher(bot, 7)
    other = await add_class(sessions, "5Б")
    await register(bot, 5, school_class="5Б")
    student = await get_user(sessions, 5)
    for payload in (f"cls:open:{other}", f"cls:st:{other}:0"):
        await bot.handle_update(cb(7, payload))
        assert "только ваши классы" in client.answers[-1]["notification"]
    for payload in (f"cls:stu:{student.id}", f"cls:hist:{student.id}:0"):
        await bot.handle_update(cb(7, payload))
        assert "не найден" in client.answers[-1]["notification"]


async def test_bot_messages_ignored(env):
    bot, client, _ = env
    update = msg(5, "/start")
    update["message"]["sender"]["is_bot"] = True
    await bot.handle_update(update)
    assert client.sent == []


# --- создание квестов ---


async def test_student_cannot_create_quest(env):
    bot, client, sessions = env
    await register(bot, 5)
    await bot.handle_update(cb(5, "menu:new_quest"))
    assert "могут учителя" in client.answers[-1]["notification"]
    assert (await get_user(sessions, 5)).state is None


async def test_teacher_creates_quest(env):
    bot, client, sessions = env
    await make_teacher(bot, 7)
    await create_quest(bot, 7, kind="photo")
    async with sessions() as s:
        quest = await s.scalar(select(Quest))
    assert (quest.title, quest.description, quest.reward, quest.kind) == (
        "Убрать класс", "Описание", 50, QuestKind.photo
    )
    assert quest.class_id is not None
    assert "Квест создан" in client.answers[-1]["text"]
    assert (await get_user(sessions, 7)).state is None


async def test_teacher_quest_fixed_range(env, fixed_ranges):
    """Учитель не вводит награду и тип: квест с проверкой, награда 50–150 на выбор проверяющего."""
    bot, client, sessions = env
    await make_teacher(bot, 7)
    await bot.handle_update(cb(7, "menu:new_quest"))
    assert "Шаг 1 из 4" in client.answers[-1]["text"]
    await bot.handle_update(msg(7, "Квест"))
    await bot.handle_update(msg(7, "Описание"))
    text, att = client.sent[-1][1], client.sent[-1][2]
    assert "Награда: 50–150 монет" in text and "Шаг 3 из 4" in text
    assert "nq:rep:none" in str(att)
    await bot.handle_update(cb(7, "nq:rep:none"))
    assert "Шаг 4 из 4" in client.answers[-1]["text"]
    await bot.handle_update(cb(7, "nq:photo:none"))
    async with sessions() as s:
        quest = await s.scalar(select(Quest))
    assert (quest.kind, quest.reward_min, quest.reward) == (QuestKind.photo, 50, 150)

    await register(bot, 5)
    sub = await submit_text(bot, sessions, 5, "Готово", title="Квест")
    await bot.handle_update(cb(7, f"rev:open:{sub.id}"))
    _, text, att = client.sent[-1]
    assert "Награда: 50–150 монет" in text
    keys = [b["payload"] for row in att[-1]["payload"]["buttons"] for b in row]
    assert keys[:5] == [f"rev:ok:{sub.id}:{a}" for a in (50, 80, 100, 120, 150)]
    await bot.handle_update(cb(7, f"rev:ok:{sub.id}:100"))
    assert (await get_user(sessions, 5)).balance == 100

    # в обход диалога — тоже нельзя
    async with sessions() as s:
        teacher = await s.scalar(select(User).where(User.max_user_id == 7))
        for kind, low, high in ((QuestKind.auto, None, 100), (QuestKind.photo, 50, 500)):
            with pytest.raises(quests_service.QuestError, match="50–150"):
                await quests_service.create_quest(
                    s, teacher, "Обход", "", high, kind, next(iter(teacher.teacher_class_ids)),
                    reward_min=low,
                )


async def test_admin_has_no_500_limit(env):
    bot, _, sessions = env
    await register(bot, ADMIN, "Админов Админ")
    await create_quest(bot, ADMIN, reward="5000")
    async with sessions() as s:
        assert (await s.scalar(select(Quest))).reward == 5000


async def test_cancel_quest_creation(env):
    bot, client, sessions = env
    await make_teacher(bot, 7)
    await bot.handle_update(cb(7, "menu:new_quest"))
    await bot.handle_update(msg(7, "Квест"))
    await bot.handle_update(cb(7, "cancel"))
    assert "отменено" in client.answers[-1]["text"]
    await bot.handle_update(cb(7, "nq:kind:auto"))
    async with sessions() as s:
        assert await s.scalar(select(Quest)) is None


# --- выполнение квестов ---


async def test_auto_quest_gives_coins_once(env):
    bot, client, sessions = env
    await make_teacher(bot, 7)
    await create_quest(bot, 7, reward="50", kind="auto")
    await register(bot, 5)
    qid = await quest_id(sessions)

    await bot.handle_update(cb(5, "menu:quests"))
    assert any(b["payload"] == f"q:open:{qid}" for b in buttons(client.answers[-1]["attachments"]))

    await bot.handle_update(cb(5, f"q:done:{qid}"))
    assert "+50 монет" in client.answers[-1]["text"]
    assert (await get_user(sessions, 5)).balance == 50

    await bot.handle_update(cb(5, f"q:done:{qid}"))
    assert "уже выполнен" in client.answers[-1]["text"]
    assert (await get_user(sessions, 5)).balance == 50

    await bot.handle_update(cb(5, "menu:quests"))
    assert "нет доступных" in client.answers[-1]["text"]


async def test_photo_quest_approved(env):
    bot, client, sessions = env
    await make_teacher(bot, 7)
    await create_quest(bot, 7, reward="30", kind="photo")
    await register(bot, 5)
    qid = await quest_id(sessions)

    # для фото-квеста «Выполнено» не работает
    await bot.handle_update(cb(5, f"q:done:{qid}"))
    assert (await get_user(sessions, 5)).balance == 0

    await bot.handle_update(cb(5, f"q:photo:{qid}"))
    await bot.handle_update(msg(5, ""))  # пустое сообщение (например, стикер)
    assert "Пришлите ответ текстом, фото или видео" in client.sent[-1][1]
    await bot.handle_update(msg(5, "", attachments=PHOTO))
    assert "на проверку" in client.sent[-1][1]

    # учитель и админ получили уведомление
    notified = {m[0] for m in client.sent if "Новая заявка" in m[1]}
    assert notified == {7, ADMIN}

    # в списке квест помечен как ожидающий
    await bot.handle_update(cb(5, "menu:quests"))
    assert any(b["text"].startswith("⏳") for b in buttons(client.answers[-1]["attachments"]))

    async with sessions() as s:
        sub_id = (await s.scalar(select(Submission))).id

    await bot.handle_update(cb(7, "menu:review"))
    assert any(
        b["payload"] == f"rev:open:{sub_id}" for b in buttons(client.answers[-1]["attachments"])
    )
    await bot.handle_update(cb(7, f"rev:open:{sub_id}"))
    to, text, att = client.sent[-1]
    assert to == 7 and att[0] == {"type": "image", "payload": {"token": "tok"}}

    await bot.handle_update(cb(7, f"rev:ok:{sub_id}"))
    assert "Подтверждено" in client.answers[-1]["text"]
    assert (await get_user(sessions, 5)).balance == 30
    assert client.sent[-1][0] == 5 and "засчитан" in client.sent[-1][1]

    # повторное подтверждение не начисляет ещё раз
    await bot.handle_update(cb(ADMIN, f"rev:ok:{sub_id}"))
    assert "уже проверена" in client.answers[-1]["notification"]
    assert (await get_user(sessions, 5)).balance == 30

    await bot.handle_update(cb(5, "menu:quests"))
    assert "нет доступных" in client.answers[-1]["text"]


async def test_photo_quest_rejected_can_retry(env):
    bot, client, sessions = env
    await make_teacher(bot, 7)
    await create_quest(bot, 7, kind="photo")
    await register(bot, 5)
    qid = await quest_id(sessions)

    await bot.handle_update(cb(5, f"q:photo:{qid}"))
    await bot.handle_update(msg(5, "", attachments=PHOTO))
    async with sessions() as s:
        sub_id = (await s.scalar(select(Submission))).id
    # «Отклонить» спрашивает причину; учитель пишет свою
    await bot.handle_update(cb(7, f"rev:no:{sub_id}"))
    prompt = client.answers[-1]
    assert "Почему отклоняете?" in prompt["text"]
    assert f"rev:nr:{sub_id}:0" in [b["payload"] for b in buttons([a for a in prompt["attachments"] if a["type"] == "inline_keyboard"])]
    await bot.handle_update(msg(7, "На фото не видно стола"))
    assert client.sent[-1][0] == 7 and "Отклонено" in client.sent[-1][1]
    note = next(text for to, text, _ in reversed(client.sent) if to == 5)
    assert "не принят" in note and "Причина: На фото не видно стола" in note
    assert (await get_user(sessions, 5)).balance == 0
    async with sessions() as s:
        assert (await s.get(Submission, sub_id)).review_comment == "На фото не видно стола"
    # в архиве причина видна
    await bot.handle_update(cb(7, f"arch:open:{sub_id}"))
    assert "💬 Причина отклонения: На фото не видно стола" in client.sent[-1][1]

    # можно отправить снова
    await bot.handle_update(cb(5, f"q:photo:{qid}"))
    await bot.handle_update(msg(5, "", attachments=PHOTO))
    async with sessions() as s:
        statuses = list(await s.scalars(select(Submission.status)))
    assert statuses == [SubmissionStatus.rejected, SubmissionStatus.pending]


async def test_reject_with_preset_reason_and_back(env):
    bot, client, sessions = env
    await make_teacher(bot, 7)
    await create_quest(bot, 7, kind="photo")
    await register(bot, 5)
    sub_id = await submit(bot, sessions, 5)
    # передумал — «Назад» возвращает кнопки проверки, ответ не отклонён
    await bot.handle_update(cb(7, f"rev:no:{sub_id}"))
    await bot.handle_update(cb(7, f"rev:back:{sub_id}"))
    keyboard_ = [a for a in client.answers[-1]["attachments"] if a["type"] == "inline_keyboard"]
    assert f"rev:ok:{sub_id}" in [b["payload"] for b in buttons(keyboard_)]
    assert (await get_user(sessions, 7)).state is None
    # готовая причина
    await bot.handle_update(cb(7, f"rev:no:{sub_id}"))
    await bot.handle_update(cb(7, f"rev:nr:{sub_id}:1"))
    assert "Причина: Не видно, что задание выполнено" in client.answers[-1]["text"]
    note = next(text for to, text, _ in reversed(client.sent) if to == 5)
    assert "Причина: Не видно, что задание выполнено" in note
    # без причины — без строки «Причина»
    second = await submit(bot, sessions, 5)
    await bot.handle_update(cb(7, f"rev:no:{second}"))
    await bot.handle_update(cb(7, f"rev:nr:{second}:0"))
    note = next(text for to, text, _ in reversed(client.sent) if to == 5)
    assert "не принят" in note and "Причина" not in note


async def test_student_cannot_review(env):
    bot, client, sessions = env
    await make_teacher(bot, 7)
    await create_quest(bot, 7, kind="photo")
    await register(bot, 5)
    await register(bot, 6, "Сидоров Пётр")
    qid = await quest_id(sessions)
    await bot.handle_update(cb(5, f"q:photo:{qid}"))
    await bot.handle_update(msg(5, "", attachments=PHOTO))
    async with sessions() as s:
        sub_id = (await s.scalar(select(Submission))).id

    await bot.handle_update(cb(6, f"rev:ok:{sub_id}"))
    assert "не найдена" in client.answers[-1]["notification"]
    assert (await get_user(sessions, 5)).balance == 0


# --- архив проверенных заявок ---


async def submit(bot, sessions, student, title="Убрать класс"):
    qid = await quest_id(sessions, title)
    await bot.handle_update(cb(student, f"q:photo:{qid}"))
    await bot.handle_update(msg(student, "", attachments=PHOTO))
    async with sessions() as s:
        return (await s.scalar(select(Submission).order_by(Submission.id.desc()))).id


async def test_decision_keeps_photo(env):
    bot, client, sessions = env
    await make_teacher(bot, 7)
    await create_quest(bot, 7, kind="photo")
    await register(bot, 5)
    sub_id = await submit(bot, sessions, 5)
    await bot.handle_update(cb(7, f"rev:ok:{sub_id}"))
    answer = client.answers[-1]
    assert answer["attachments"][0] == {"type": "image", "payload": {"token": "tok"}}
    assert "Иванов Иван" in answer["text"]


async def test_archive(env):
    bot, client, sessions = env
    await make_teacher(bot, 7)
    await create_quest(bot, 7, kind="photo")
    await register(bot, 5)
    first = await submit(bot, sessions, 5)
    await bot.handle_update(cb(7, f"rev:no:{first}"))
    await bot.handle_update(cb(7, f"rev:nr:{first}:0"))  # без причины
    second = await submit(bot, sessions, 5)

    # на проверке ещё одна, в архиве только отклонённая
    await bot.handle_update(cb(7, "arch:page:0"))
    payloads = [b["payload"] for b in buttons(client.answers[-1]["attachments"])]
    assert f"arch:open:{first}" in payloads and f"arch:open:{second}" not in payloads

    await bot.handle_update(cb(ADMIN, f"rev:ok:{second}"))
    await bot.handle_update(cb(7, "arch:page:0"))
    opened = [
        b["payload"] for b in buttons(client.answers[-1]["attachments"])
        if b["payload"].startswith("arch:open:")
    ]
    assert opened == [f"arch:open:{second}", f"arch:open:{first}"]  # новые сверху

    await bot.handle_update(cb(7, f"arch:open:{second}"))
    to, text, att = client.sent[-1]
    assert to == 7 and att[0]["type"] == "image"
    assert "✅ Подтверждена, начислено 50 монет" in text and "Админов Админ" in text

    # заявку на проверке через архив не открыть, ученику архив недоступен
    await bot.handle_update(cb(5, "arch:page:0"))
    assert "учителям и админам" in client.answers[-1]["notification"]


async def test_archive_pages(env):
    bot, client, sessions = env
    await register(bot, ADMIN, "Админов Админ")
    await create_quest(bot, ADMIN, kind="photo")
    for uid in range(200, 212):
        await register(bot, uid, "Ученик Тестовый")
        sub_id = await submit(bot, sessions, uid)
        await bot.handle_update(cb(ADMIN, f"rev:ok:{sub_id}"))

    await bot.handle_update(cb(ADMIN, "arch:page:0"))
    page0 = [b["payload"] for b in buttons(client.answers[-1]["attachments"])]
    assert sum(p.startswith("arch:open:") for p in page0) == 10
    assert "arch:page:1" in page0

    await bot.handle_update(cb(ADMIN, "arch:page:1"))
    page1 = [b["payload"] for b in buttons(client.answers[-1]["attachments"])]
    assert sum(p.startswith("arch:open:") for p in page1) == 2
    assert "arch:page:0" in page1 and "arch:page:2" not in page1


# --- рейтинг ---


async def award(sessions, uid, amount):
    """Начисляет ученику amount монет через подтверждённую заявку."""
    async with sessions() as s:
        user = await s.scalar(select(User).where(User.max_user_id == uid))
        quest = Quest(title="Q", reward=amount, kind=QuestKind.auto, author_id=user.id)
        s.add(quest)
        await s.flush()
        s.add(Submission(quest_id=quest.id, user_id=user.id, status=SubmissionStatus.approved))
        user.balance += amount
        await s.commit()


async def test_school_rating(env):
    bot, client, sessions = env
    await register(bot, ADMIN, "Админов Админ")
    scores = {1: ("Альфа Аня", 300), 2: ("Бета Боря", 200), 3: ("Вега Вера", 200),
              4: ("Гамма Галя", 100), 5: ("Дельта Дима", 0)}
    for uid, (name, amount) in scores.items():
        await register(bot, uid, name)
        if amount:
            await award(sessions, uid, amount)
    # рейтинг по текущему балансу: потраченные монеты не учитываются
    async with sessions() as s:
        (await s.scalar(select(User).where(User.max_user_id == 1))).balance = 250
        await s.commit()

    await bot.handle_update(cb(4, "menu:rating"))
    text = client.answers[-1]["text"]
    lines = text.splitlines()
    assert "По текущему балансу монет" in text
    assert "🥇 Альфа Аня — 250" in lines
    assert "🥈 Бета Боря — 200" in lines and "🥈 Вега Вера — 200" in lines  # ничья
    assert "4. Гамма Галя — 100 ← вы" in lines
    assert lines.index("―――――――――――") == lines.index("🥈 Вега Вера — 200") + 1
    assert "Ваше место: 4 из 5 (100 монет)" in text
    assert "Админов" not in text  # админ не участвует


async def test_rating_shows_own_place_outside_top(env):
    bot, client, sessions = env
    for uid in range(1, 13):
        await register(bot, uid, f"Ученик Номер{chr(ord('а') + uid)}")
        await award(sessions, uid, 1000 - uid)
    await register(bot, 50, "Последний Пётр")
    await bot.handle_update(cb(50, "menu:rating"))
    text = client.answers[-1]["text"]
    lines = text.split("🏁")[0].splitlines()  # без блока соревнования классов
    assert lines.count("…") == 1
    assert "13. Последний Пётр — 0 ← вы" in lines
    rows = [l for l in lines if " — " in l and not l.startswith("Итоги")]
    assert len(rows) == 11  # топ-10 + своя строка


async def test_class_rating(env):
    bot, client, sessions = env
    await register(bot, ADMIN, "Админов Админ")
    await register(bot, 1, "Альфа Аня")
    await register(bot, 2, "Бета Боря")
    await award(sessions, 1, 50)
    await award(sessions, 2, 70)
    async with sessions() as s:
        other = SchoolClass(name="5Б")
        s.add(other)
        await s.flush()
        (await s.scalar(select(User).where(User.max_user_id == 2))).class_id = other.id
        await s.commit()
        test_class = await s.scalar(select(SchoolClass).where(SchoolClass.name == "Тестовый класс"))

    # ученик видит школу и только свой класс
    await bot.handle_update(cb(1, "menu:rating"))
    payloads = [b["payload"] for b in buttons(client.answers[-1]["attachments"])]
    assert f"rate:class:{test_class.id}" in payloads and f"rate:class:{other.id}" not in payloads

    await bot.handle_update(cb(1, f"rate:class:{test_class.id}"))
    text = client.answers[-1]["text"]
    assert "«Тестовый класс»" in text and "Альфа Аня" in text and "Бета Боря" not in text
    assert "Ваше место: 1 из 1" in text

    await bot.handle_update(cb(1, f"rate:class:{other.id}"))
    assert "только вашего класса" in client.answers[-1]["notification"]

    await bot.handle_update(cb(1, "rate:classes"))
    assert "только вашего класса" in client.answers[-1]["notification"]

    # админ: вместо списка классов — одна кнопка «По классам», за ней все классы
    await bot.handle_update(cb(ADMIN, "menu:rating"))
    payloads = [b["payload"] for b in buttons(client.answers[-1]["attachments"])]
    assert "rate:classes" in payloads and not any(p.startswith("rate:class:") for p in payloads)
    await bot.handle_update(cb(ADMIN, "rate:classes"))
    payloads = [b["payload"] for b in buttons(client.answers[-1]["attachments"])]
    assert {f"rate:class:{test_class.id}", f"rate:class:{other.id}", "rate:school"} <= set(payloads)
    await bot.handle_update(cb(ADMIN, f"rate:class:{other.id}"))
    text = client.answers[-1]["text"]
    assert "🥇 Бета Боря — 70" in text and "Ваше место" not in text
    labels = [b["text"] for b in buttons(client.answers[-1]["attachments"])]
    assert "• 👥 По классам · 5Б •" in labels


# --- админы без класса ---


async def test_greeting_without_project_name(env):
    bot, client, _ = env
    await register(bot, 5)
    await bot.handle_update(msg(5, "/start"))
    text = client.sent[-1][1]
    assert "Сферум" not in text
    assert "Привет, Иванов Иван!" in text and "Ваша роль: ученик" in text


async def test_admin_has_no_class(env):
    bot, client, sessions = env
    await register(bot, ADMIN, "Админов Админ")
    admin = await get_user(sessions, ADMIN)
    assert admin.class_id is None
    assert "Класс:" not in client.sent[-1][1]
    assert "регистрация завершена" in client.sent[-1][1]


async def test_admin_from_env_leaves_class(env):
    bot, client, sessions = env
    await register(bot, ADMIN, "Админов Админ")
    await register(bot, 5)  # создаёт тестовый класс
    async with sessions() as s:
        test_class = await s.scalar(select(SchoolClass))
        (await s.scalar(select(User).where(User.max_user_id == ADMIN))).class_id = test_class.id
        await s.commit()
    await bot.handle_update(msg(ADMIN, "/start"))
    assert (await get_user(sessions, ADMIN)).class_id is None
    assert "Класс:" not in client.sent[-1][1]


async def test_role_admin_removes_class_and_back(env):
    bot, client, sessions = env
    await register(bot, ADMIN, "Админов Админ")
    await register(bot, 5)
    await bot.handle_update(msg(ADMIN, "/role 5 admin"))
    assert (await get_user(sessions, 5)).class_id is None
    await bot.handle_update(msg(5, "/start"))
    assert "Класс:" not in client.sent[-1][1]

    # учителю классы назначает админ
    await bot.handle_update(msg(ADMIN, "/role 5 teacher"))
    assert "Назначьте ему классы" in client.sent[-1][1]
    await bot.handle_update(msg(5, "/start"))
    assert "Классы: пока не назначены" in client.sent[-1][1]

    # ученик без класса заново проходит регистрацию и выбирает класс
    await bot.handle_update(msg(ADMIN, "/role 5 student"))
    assert "выберет класс" in client.sent[-1][1]
    assert (await get_user(sessions, 5)).full_name is None
    await bot.handle_update(msg(5, "/start"))
    assert "Кто вы?" in client.sent[-1][1]


async def test_admin_quest_visible_to_all(env):
    bot, client, sessions = env
    await register(bot, ADMIN, "Админов Админ")
    await create_quest(bot, ADMIN)
    assert "всей школе" in client.answers[-1]["text"]
    await register(bot, 5)
    await bot.handle_update(cb(5, "menu:quests"))
    qid = await quest_id(sessions)
    assert any(b["payload"] == f"q:open:{qid}" for b in buttons(client.answers[-1]["attachments"]))


# --- аудитория квеста ---


async def add_class(sessions, name, uid=None):
    """Создаёт класс и, если указан uid, переводит в него пользователя."""
    async with sessions() as s:
        school_class = SchoolClass(name=name)
        s.add(school_class)
        await s.flush()
        if uid is not None:
            (await s.scalar(select(User).where(User.max_user_id == uid))).class_id = school_class.id
        await s.commit()
        return school_class.id


async def visible_quests(bot, client, uid):
    await bot.handle_update(cb(uid, "menu:quests"))
    attachments = client.answers[-1]["attachments"] or []
    return [b["text"].split(" · ")[0] for b in buttons(attachments) if b["payload"].startswith("q:open:")] if attachments else []


async def test_admin_chooses_class(env):
    bot, client, sessions = env
    await register(bot, ADMIN, "Админов Админ")
    await register(bot, 5)
    await register(bot, 6, "Сидоров Пётр")
    other = await add_class(sessions, "5Б", uid=6)

    await bot.handle_update(cb(ADMIN, "menu:new_quest"))
    assert "Шаг 1 из 7" in client.answers[-1]["text"]
    await bot.handle_update(msg(ADMIN, "Для 5Б"))
    await bot.handle_update(msg(ADMIN, "Описание"))
    await bot.handle_update(msg(ADMIN, "1000"))
    await bot.handle_update(cb(ADMIN, "nq:kind:auto"))
    await bot.handle_update(cb(ADMIN, "nq:rep:none"))
    text, att = client.answers[-1]["text"], client.answers[-1]["attachments"]
    assert "Шаг 6 из 7" in text
    payloads = [b["payload"] for b in buttons(att)]
    assert payloads[0] == "nq:aud:all" and f"nq:aud:{other}" in payloads
    # текст вместо кнопки — подсказка, квест не создан
    await bot.handle_update(msg(ADMIN, "5Б"))
    assert "для кого квест" in client.sent[-1][1]
    await bot.handle_update(cb(ADMIN, f"nq:aud:{other}"))
    assert "Шаг 7 из 7" in client.answers[-1]["text"]
    await bot.handle_update(cb(ADMIN, "nq:photo:none"))
    assert "ученикам класса «5Б»" in client.answers[-1]["text"]

    await create_quest(bot, ADMIN, title="Для всех")

    assert await visible_quests(bot, client, 6) == ["Для всех", "Для 5Б"]  # 50 < 1000
    assert await visible_quests(bot, client, 5) == ["Для всех"]


async def test_admin_audience_unknown_class(env):
    bot, client, sessions = env
    await register(bot, ADMIN, "Админов Админ")
    await create_quest(bot, ADMIN, audience="999")
    assert "Класс не найден" in client.answers[-2]["notification"]  # затем шаг картинки не начат
    async with sessions() as s:
        assert await s.scalar(select(Quest)) is None


async def shift_submissions(sessions, days):
    """Сдвинуть все заявки в прошлое: как будто прошло days дней."""
    async with sessions() as s:
        for sub in await s.scalars(select(Submission)):
            sub.created_at = sub.created_at - timedelta(days=days)
        await s.commit()


async def test_daily_quest_repeats_next_day(env):
    bot, client, sessions = env
    await make_teacher(bot, 7)
    await create_quest(bot, 7, title="Зарядка", repeat="daily")
    assert "Повтор: раз в день" in client.answers[-1]["text"]
    await register(bot, 5)
    qid = await quest_id(sessions, "Зарядка")

    await bot.handle_update(cb(5, "menu:quests"))
    assert "🔁 Зарядка · 50" in [b["text"] for b in buttons(client.answers[-1]["attachments"])]
    await bot.handle_update(cb(5, f"q:open:{qid}"))
    assert "🔁 Можно выполнять раз в день" in client.answers[-1]["text"]
    await bot.handle_update(cb(5, f"q:done:{qid}"))
    assert "Снова выполнить можно завтра" in client.answers[-1]["text"]
    # сегодня второй раз нельзя, в списке квеста нет
    await bot.handle_update(cb(5, f"q:done:{qid}"))
    assert "снова можно завтра" in client.answers[-1]["text"]
    assert await visible_quests(bot, client, 5) == []

    await shift_submissions(sessions, 1)  # наступил следующий день
    assert await visible_quests(bot, client, 5) == ["🔁 Зарядка"]
    await bot.handle_update(cb(5, f"q:done:{qid}"))
    assert (await get_user(sessions, 5)).balance == 100


async def test_one_time_quest_stays_done(env):
    bot, client, sessions = env
    await make_teacher(bot, 7)
    await create_quest(bot, 7)
    await register(bot, 5)
    await bot.handle_update(cb(5, f"q:done:{await quest_id(sessions)}"))
    await shift_submissions(sessions, 400)
    assert await visible_quests(bot, client, 5) == []


async def test_weekly_review_quest(env):
    bot, client, sessions = env
    await make_teacher(bot, 7)
    await create_quest(bot, 7, title="Отчёт", kind="photo", repeat="weekly")
    await register(bot, 5)
    sub = await submit_text(bot, sessions, 5, "Сделал", title="Отчёт")
    # пока ответ на проверке, второй отправить нельзя, даже в новом периоде
    await shift_submissions(sessions, 8)
    await bot.handle_update(cb(5, f"q:photo:{await quest_id(sessions, 'Отчёт')}"))
    assert "ждёт проверки" in client.answers[-1]["notification"]

    await bot.handle_update(cb(7, f"rev:ok:{sub.id}"))
    note = next(text for to, text, _ in reversed(client.sent) if to == 5)
    assert "засчитан" in note and "Снова выполнить можно с понедельника" in note
    # ответ отправлен на прошлой неделе — квест уже снова доступен
    assert await visible_quests(bot, client, 5) == ["🔁 Отчёт"]


def test_period_start():
    msk_night = datetime(2026, 10, 18, 22, 30, tzinfo=timezone.utc)  # 19.10, 01:30 по Москве
    day = quests_service.period_start(QuestRepeat.daily, msk_night)
    assert day == datetime(2026, 10, 18, 21, 0, tzinfo=timezone.utc)  # полночь 19.10 по Москве
    week = quests_service.period_start(QuestRepeat.weekly, msk_night)
    assert week == day  # 19.10.2026 — понедельник
    sunday = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)
    assert quests_service.period_start(QuestRepeat.weekly, sunday) == datetime(
        2026, 10, 11, 21, 0, tzinfo=timezone.utc
    )
    assert quests_service.period_start(QuestRepeat.monthly, sunday) == datetime(
        2026, 9, 30, 21, 0, tzinfo=timezone.utc
    )
    assert quests_service.period_start(QuestRepeat.none, sunday) is None


async def test_teacher_has_six_steps_and_own_class(env):
    bot, client, sessions = env
    await make_teacher(bot, 7)
    await bot.handle_update(cb(7, "menu:new_quest"))
    assert "Шаг 1 из 6" in client.answers[-1]["text"]
    await bot.handle_update(cb(7, "cancel"))
    await create_quest(bot, 7)
    assert "ученикам класса «Тестовый класс»" in client.answers[-1]["text"]
    # кнопка выбора класса учителю не поможет
    await bot.handle_update(cb(7, "nq:aud:all"))
    assert "уже завершено" in client.answers[-1]["notification"]
    async with sessions() as s:
        quest = await s.scalar(select(Quest))
    assert quest.class_id == await class_id(sessions)


# --- управление квестами ---


def payloads_of(answer):
    return [b["payload"] for b in buttons(answer["attachments"])]


async def test_teacher_closes_own_quest(env):
    bot, client, sessions = env
    await make_teacher(bot, 7)
    await create_quest(bot, 7, title="Мой квест", kind="photo")
    await register(bot, 5)
    qid = await quest_id(sessions, "Мой квест")
    sub_id = await submit(bot, sessions, 5, "Мой квест")

    await bot.handle_update(cb(7, "mq:list:a:0"))
    assert f"mq:open:{qid}" in payloads_of(client.answers[-1])

    await bot.handle_update(cb(7, f"mq:open:{qid}"))
    text = client.answers[-1]["text"]
    assert "Статус: 🟢 активен" in text and "Ждут проверки: 1" in text
    assert "Автор" not in text  # свой квест

    await bot.handle_update(cb(7, f"mq:close:{qid}"))
    assert "Закрыть квест" in client.answers[-1]["text"]
    assert f"mq:close_ok:{qid}" in payloads_of(client.answers[-1])
    async with sessions() as s:
        assert (await s.get(Quest, qid)).is_active  # без подтверждения не закрыт

    await bot.handle_update(cb(7, f"mq:close_ok:{qid}"))
    assert "🔒 Квест закрыт" in client.answers[-1]["text"]
    assert f"mq:reopen:{qid}" in payloads_of(client.answers[-1])

    # ученик квест не видит и не может выполнить
    assert await visible_quests(bot, client, 5) == []
    await bot.handle_update(cb(5, f"q:photo:{qid}"))
    assert "закрыт" in client.answers[-1]["notification"]

    # заявку, отправленную до закрытия, можно подтвердить
    await bot.handle_update(cb(7, f"rev:ok:{sub_id}"))
    assert (await get_user(sessions, 5)).balance == 50

    # в списке закрытых, а среди активных его нет
    await bot.handle_update(cb(7, "mq:list:a:0"))
    assert f"mq:open:{qid}" not in payloads_of(client.answers[-1])
    await bot.handle_update(cb(7, "mq:list:c:0"))
    assert f"mq:open:{qid}" in payloads_of(client.answers[-1])

    # повторное закрытие и повторное открытие
    await bot.handle_update(cb(7, f"mq:close_ok:{qid}"))
    assert "уже закрыт" in client.answers[-1]["notification"]
    await bot.handle_update(cb(7, f"mq:reopen:{qid}"))
    assert "снова открыт" in client.answers[-1]["text"]
    async with sessions() as s:
        assert (await s.get(Quest, qid)).is_active


async def test_teacher_cannot_close_others_quest(env):
    bot, client, sessions = env
    await make_teacher(bot, 7)
    await register(bot, 8, "Другая Учительница")
    await bot.handle_update(msg(ADMIN, "/role 8 teacher"))
    await assign_class(bot, 8)
    await create_quest(bot, 8, title="Чужой")
    await create_quest(bot, ADMIN, title="Админский")
    other, admins = await quest_id(sessions, "Чужой"), await quest_id(sessions, "Админский")
    # квест коллеги для общего класса учитель видит, но без кнопок управления
    await bot.handle_update(cb(7, f"mq:open:{other}"))
    answer = client.answers[-1]
    assert "Это квест коллеги (Другая Учительница)" in answer["text"]
    assert f"mq:close:{other}" not in payloads_of(answer)
    # квест админа — тоже
    await bot.handle_update(cb(7, f"mq:open:{admins}"))
    answer = client.answers[-1]
    assert "квест администрации" in answer["text"]
    assert f"mq:close:{admins}" not in payloads_of(answer)
    for qid in (other, admins):
        await bot.handle_update(cb(7, f"mq:close_ok:{qid}"))
        assert "не найден" in client.answers[-1]["notification"]
        async with sessions() as s:
            assert (await s.get(Quest, qid)).is_active

    await bot.handle_update(cb(7, "mq:list:a:0"))
    answer = client.answers[-1]
    assert "все квесты, которые видят ваши ученики" in answer["text"]
    labels = [b["text"] for b in buttons(answer["attachments"])]
    assert any(t.startswith("🏫 Админский") for t in labels)
    assert any(t.startswith("👥 Чужой") for t in labels)


async def test_teacher_does_not_see_other_class_quests(env):
    bot, client, sessions = env
    await make_teacher(bot, 7)
    await add_class(sessions, "5Б")
    await register(bot, 8, "Другая Учительница")
    await bot.handle_update(msg(ADMIN, "/role 8 teacher"))
    await assign_class(bot, 8, "5Б")
    await create_quest(bot, 8, title="Для 5Б")
    qid = await quest_id(sessions, "Для 5Б")
    await bot.handle_update(cb(7, f"mq:open:{qid}"))
    assert "не найден" in client.answers[-1]["notification"]
    await bot.handle_update(cb(7, "mq:list:a:0"))
    assert f"mq:open:{qid}" not in payloads_of(client.answers[-1])


async def admin_photo_quest_answer(bot, sessions, student=5, school_class=TEST_CLASS):
    """Учитель 7 ведёт Тестовый класс; админ создаёт квест на всю школу; ученик отвечает."""
    await make_teacher(bot, 7)
    await create_quest(bot, ADMIN, title="Зарядка", kind="photo")
    await register(bot, student, school_class=school_class)
    return await submit_text(bot, sessions, student, "Сделал", title="Зарядка")


async def test_teacher_reviews_admin_quest(env):
    bot, client, sessions = env
    sub = await admin_photo_quest_answer(bot, sessions)
    # учитель класса уведомление не получает (квест не его), но видит заявку в «На проверке»
    assert not any(to == 7 and "Новая заявка" in text for to, text, _ in client.sent)
    await bot.handle_update(cb(7, "menu:review"))
    assert f"rev:open:{sub.id}" in payloads_of(client.answers[-1])

    client.sent.clear()
    await bot.handle_update(cb(7, f"rev:ok:{sub.id}"))
    assert (await get_user(sessions, 5)).balance == 50
    # админ получил решение учителя с кнопкой изменить его
    to, text, att = next(m for m in client.sent if m[0] == ADMIN)
    assert "Учитель Учителева Анна" in text and "✅ Засчитано" in text
    assert f"rev:flip:{sub.id}" in [b["payload"] for b in buttons(att)]


async def test_teacher_cannot_review_other_class_in_admin_quest(env):
    bot, client, sessions = env
    await add_class(sessions, "5Б")
    sub = await admin_photo_quest_answer(bot, sessions, school_class="5Б")
    assert not any(to == 7 and "Новая заявка" in text for to, text, _ in client.sent)
    await bot.handle_update(cb(7, f"rev:ok:{sub.id}"))
    assert "не найдена" in client.answers[-1]["notification"]
    await bot.handle_update(cb(ADMIN, f"rev:ok:{sub.id}"))  # админ проверяет всех
    assert (await get_user(sessions, 5)).balance == 50


async def test_admin_overrides_teacher_decision(env):
    bot, client, sessions = env
    sub = await admin_photo_quest_answer(bot, sessions)
    await bot.handle_update(cb(7, f"rev:ok:{sub.id}"))
    client.sent.clear()

    await bot.handle_update(cb(ADMIN, f"rev:flip:{sub.id}"))
    assert "не засчитано, списано 50" in client.answers[-1]["text"]
    assert (await get_user(sessions, 5)).balance == 0
    texts = {to: text for to, text, _ in client.sent}
    assert "не засчитан" in texts[5] and "изменил(а) ваше решение" in texts[7]
    async with sessions() as s:
        assert (await s.get(Submission, sub.id)).status == SubmissionStatus.rejected

    # и обратно
    await bot.handle_update(cb(ADMIN, f"rev:flip:{sub.id}"))
    assert "засчитано, ученику начислено 50" in client.answers[-1]["text"]
    assert (await get_user(sessions, 5)).balance == 50


async def test_override_does_not_go_below_zero(env):
    bot, client, sessions = env
    sub = await admin_photo_quest_answer(bot, sessions)
    await bot.handle_update(cb(7, f"rev:ok:{sub.id}"))
    async with sessions() as s:
        student = await s.scalar(select(User).where(User.max_user_id == 5))
        student.balance = 20  # часть монет уже потрачена
        await s.commit()
    await bot.handle_update(cb(ADMIN, f"rev:flip:{sub.id}"))
    assert "списано 20 монет из 50 монет" in client.answers[-1]["text"]
    assert (await get_user(sessions, 5)).balance == 0


async def test_only_admin_overrides(env):
    bot, client, sessions = env
    sub = await admin_photo_quest_answer(bot, sessions)
    await bot.handle_update(cb(7, f"rev:ok:{sub.id}"))
    await bot.handle_update(cb(7, f"rev:flip:{sub.id}"))
    assert "только админ" in client.answers[-1]["notification"]
    # в архиве у учителя кнопки изменения нет, у админа есть
    await bot.handle_update(cb(7, f"arch:open:{sub.id}"))
    assert f"rev:flip:{sub.id}" not in [b["payload"] for b in buttons(client.sent[-1][2])]
    await bot.handle_update(cb(ADMIN, f"arch:open:{sub.id}"))
    assert f"rev:flip:{sub.id}" in [b["payload"] for b in buttons(client.sent[-1][2])]


async def test_no_admin_notice_for_teacher_own_quest(env):
    bot, client, sessions = env
    await make_teacher(bot, 7)
    await create_quest(bot, 7, kind="photo")
    await register(bot, 5)
    sub = await submit_text(bot, sessions, 5, "Готово")
    client.sent.clear()
    await bot.handle_update(cb(7, f"rev:ok:{sub.id}"))
    assert not any(to == ADMIN for to, _, _ in client.sent)


async def test_admin_manages_all_quests(env):
    bot, client, sessions = env
    await make_teacher(bot, 7)
    await create_quest(bot, 7, title="Учительский")
    qid = await quest_id(sessions, "Учительский")

    await bot.handle_update(cb(ADMIN, "mq:list:a:0"))
    assert "Все квесты" in client.answers[-1]["text"]
    assert f"mq:open:{qid}" in payloads_of(client.answers[-1])

    await bot.handle_update(cb(ADMIN, f"mq:open:{qid}"))
    assert "Автор: Учителева Анна" in client.answers[-1]["text"]
    await bot.handle_update(cb(ADMIN, f"mq:close_ok:{qid}"))
    async with sessions() as s:
        assert not (await s.get(Quest, qid)).is_active


async def test_student_cannot_manage(env):
    bot, client, sessions = env
    await register(bot, ADMIN, "Админов Админ")
    await create_quest(bot, ADMIN)
    await register(bot, 5)
    qid = await quest_id(sessions)
    await bot.handle_update(cb(5, f"mq:close_ok:{qid}"))
    assert "админам" in client.answers[-1]["notification"]
    async with sessions() as s:
        assert (await s.get(Quest, qid)).is_active


async def test_manage_pages(env):
    bot, client, sessions = env
    await register(bot, ADMIN, "Админов Админ")
    for i in range(12):
        await create_quest(bot, ADMIN, title=f"Квест {i:02}")
    await bot.handle_update(cb(ADMIN, "mq:list:a:0"))
    page0 = payloads_of(client.answers[-1])
    assert sum(p.startswith("mq:open:") for p in page0) == 10
    assert "mq:list:a:1" in page0
    await bot.handle_update(cb(ADMIN, "mq:list:a:1"))
    page1 = payloads_of(client.answers[-1])
    assert sum(p.startswith("mq:open:") for p in page1) == 2
    assert "mq:list:a:0" in page1


# --- перезапуск ---


async def test_restart_button_in_every_menu(env):
    bot, client, _ = env
    await register(bot, ADMIN, "Админов Админ")
    await make_teacher(bot, 7)
    await register(bot, 5)
    for uid in (ADMIN, 7, 5):
        await bot.handle_update(msg(uid, "/start"))
        assert "menu:restart" in [b["payload"] for b in buttons(client.sent[-1][2])]


async def test_restart_deletes_bot_messages_and_resets(env):
    bot, client, sessions = env
    await make_teacher(bot, 7)
    await bot.handle_update(cb(7, "menu:new_quest"))
    await bot.handle_update(msg(7, "Недописанный квест"))
    teacher_mids = {f"mid.{i + 1}" for i, m in enumerate(client.sent) if m[0] == 7}
    admin_mids = {f"mid.{i + 1}" for i, m in enumerate(client.sent) if m[0] == ADMIN}
    client.fail_delete = {min(teacher_mids)}  # одно сообщение удалить не выйдет

    await bot.handle_update(cb(7, "menu:restart"))
    assert client.answers[-1]["notification"] == "Перезапуск…"
    assert set(client.deleted) == teacher_mids - client.fail_delete
    assert not admin_mids & set(client.deleted)  # чужие сообщения не трогаем
    assert (await get_user(sessions, 7)).state is None
    assert client.sent[-1][0] == 7 and "Ваша роль: учитель" in client.sent[-1][1]

    # после перезапуска текст не продолжает создание квеста
    await bot.handle_update(msg(7, "500"))
    async with sessions() as s:
        assert await s.scalar(select(Quest)) is None

    # второй перезапуск удаляет только то, что пришло после первого
    client.deleted.clear()
    await bot.handle_update(cb(7, "menu:restart"))
    assert len(client.deleted) == 2  # меню после перезапуска + ответ на «500»


async def test_restart_unregistered_asks_name(env):
    bot, client, _ = env
    await bot.handle_update(msg(5, "/start"))
    await bot.handle_update(cb(5, "menu:restart"))
    assert client.deleted == ["mid.1"]
    assert "Кто вы?" in client.sent[-1][1]


# --- уведомления о новых квестах ---


def notices(client):
    return [m for m in client.sent if m[1].startswith("🆕 Новый квест")]


async def test_new_quest_notifies_class(env):
    bot, client, sessions = env
    await make_teacher(bot, 7)
    await register(bot, 5)
    await register(bot, 6, "Сидоров Пётр")
    await add_class(sessions, "5Б", uid=6)
    await bot.handle_update(msg(9, "/start"))  # не ввёл ФИО — не получает

    await create_quest(bot, 7, title="Классный", reward="40")
    assert "Уведомление получат учеников: 1" in client.answers[-1]["text"]
    sent = notices(client)
    assert [m[0] for m in sent] == [5]
    assert "«Классный»" in sent[0][1] and "40 монет" in sent[0][1]
    qid = await quest_id(sessions, "Классный")
    assert buttons(sent[0][2])[0]["payload"] == f"q:open:{qid}"

    # кнопка из уведомления открывает карточку квеста
    await bot.handle_update(cb(5, f"q:open:{qid}"))
    assert "🎯 Классный" in client.answers[-1]["text"]


async def test_school_quest_notifies_all_students_only(env):
    bot, client, sessions = env
    await make_teacher(bot, 7)
    await register(bot, 5)
    await register(bot, 6, "Сидоров Пётр")
    await add_class(sessions, "5Б", uid=6)
    await create_quest(bot, ADMIN, title="Для всех")
    assert sorted(m[0] for m in notices(client)) == [5, 6]  # учителя и админа нет


async def test_admin_class_quest_notifies_that_class(env):
    bot, client, sessions = env
    await register(bot, ADMIN, "Админов Админ")
    await register(bot, 5)
    await register(bot, 6, "Сидоров Пётр")
    other = await add_class(sessions, "5Б", uid=6)
    await create_quest(bot, ADMIN, audience=f"class:{other}")
    assert [m[0] for m in notices(client)] == [6]


async def test_notifications_in_background(env):
    _, client, sessions = env
    bot = Bot(client, sessions, admin_ids={ADMIN})  # фоновый режим, как в бою
    await register(bot, ADMIN, "Админов Админ")
    for uid in (5, 6, 7):
        await register(bot, uid, "Ученик Тестовый")
    await create_quest(bot, ADMIN)
    await bot.wait_background()
    assert sorted(m[0] for m in notices(client)) == [5, 6, 7]


# --- оповещения ---


def announcements(client):
    return [m for m in client.sent if m[1].startswith("📣 Оповещение") and "Получатели:" not in m[1]]


async def school_with_two_classes(bot, sessions):
    """Админ, учитель и ученик тестового класса, ученик 5Б."""
    await make_teacher(bot, 7)
    await register(bot, 5)
    await register(bot, 6, "Сидоров Пётр")
    return await add_class(sessions, "5Б", uid=6)


async def test_broadcast_to_school(env):
    bot, client, sessions = env
    await school_with_two_classes(bot, sessions)

    await bot.handle_update(cb(ADMIN, "menu:broadcast"))
    assert "bc:new" in payloads_of(client.answers[-1])
    await bot.handle_update(cb(ADMIN, "bc:new"))
    await bot.handle_update(msg(ADMIN, "Завтра линейка в 9:00"))
    assert "Кому отправить" in client.sent[-1][1]
    await bot.handle_update(cb(ADMIN, "bc:aud:all"))
    preview = client.answers[-1]["text"]
    assert "Завтра линейка в 9:00" in preview and "вся школа (ученики и учителя), 3 чел." in preview
    assert not announcements(client)  # до подтверждения ничего не ушло

    await bot.handle_update(cb(ADMIN, "bc:send"))
    assert "Получателей: 3" in client.answers[-1]["text"]
    sent = announcements(client)
    assert sorted(m[0] for m in sent) == [5, 6, 7]  # ученики и учитель, без автора
    assert sent[0][1] == (
        "📣 Оповещение\nОт: Админов Админ (админ)\nКому: всем ученикам и учителям\n\n"
        "Завтра линейка в 9:00"
    )
    assert (await get_user(sessions, ADMIN)).state is None

    # повторное нажатие не рассылает второй раз
    await bot.handle_update(cb(ADMIN, "bc:send"))
    assert "уже отправлено" in client.answers[-1]["notification"]
    assert len(announcements(client)) == 3


async def test_broadcast_to_class_with_photo(env):
    bot, client, sessions = env
    other = await school_with_two_classes(bot, sessions)
    await bot.handle_update(cb(ADMIN, "bc:new"))
    await bot.handle_update(msg(ADMIN, "Фото с экскурсии", attachments=PHOTO))
    await bot.handle_update(cb(ADMIN, f"bc:aud:class:{other}"))
    answer = client.answers[-1]
    assert "ученики класса «5Б», 1 чел." in answer["text"]
    assert answer["attachments"][0] == {"type": "image", "payload": {"token": "tok"}}
    await bot.handle_update(cb(ADMIN, "bc:send"))
    sent = announcements(client)
    assert [m[0] for m in sent] == [6]
    assert sent[0][2][0] == {"type": "image", "payload": {"token": "tok"}}
    assert buttons(sent[0][2][1:])[0]["text"] == "✅ Прочитано"

    # «Прочитано»: фото и текст остаются, кнопка уходит, приходит меню
    read = buttons(sent[0][2][1:])[0]["payload"]
    await bot.handle_update(cb(6, read))
    answer = client.answers[-1]
    assert answer["text"] == (
        "📣 Оповещение\nОт: Админов Админ (админ)\nКому: ученикам класса «5Б»\n\nФото с экскурсии"
    )
    assert answer["attachments"] == [{"type": "image", "payload": {"token": "tok"}}]
    assert client.sent[-1][0] == 6 and "Ваша роль: ученик" in client.sent[-1][1]


async def test_broadcast_validation_and_cancel(env):
    bot, client, sessions = env
    await school_with_two_classes(bot, sessions)
    await bot.handle_update(cb(ADMIN, "bc:new"))
    await bot.handle_update(msg(ADMIN, ""))
    assert "Пришлите текст" in client.sent[-1][1]
    await bot.handle_update(msg(ADMIN, "я" * 3501))
    assert "Слишком длинный" in client.sent[-1][1]
    await bot.handle_update(msg(ADMIN, "Текст"))
    await bot.handle_update(msg(ADMIN, "всем"))  # текст вместо кнопки
    assert "Выберите кнопкой" in client.sent[-1][1]
    await bot.handle_update(cb(ADMIN, "bc:aud:class:999"))
    assert "Класс не найден" in client.answers[-1]["notification"]
    await bot.handle_update(cb(ADMIN, "cancel"))
    await bot.handle_update(cb(ADMIN, "bc:aud:all"))
    await bot.handle_update(cb(ADMIN, "bc:send"))
    assert not announcements(client)


async def test_broadcast_audiences(env):
    """Ученикам, учителям, модераторам, классу и одному ученику."""
    bot, client, sessions = env
    other = await school_with_two_classes(bot, sessions)
    await register(bot, 30, "Модеров Максим")
    await bot.handle_update(msg(ADMIN, "/role 30 moderator"))
    await register(bot, 8, "Андреев Андрей", school_class="5Б")
    student = await get_user(sessions, 8)

    await bot.handle_update(cb(ADMIN, "bc:new"))
    await bot.handle_update(msg(ADMIN, "Привет"))
    choices = payloads_of({"attachments": client.sent[-1][2]})
    assert {"bc:aud:all", "bc:aud:students", "bc:aud:teachers", "bc:aud:moderators",
            "bc:aud:cls", "bc:aud:pick"} <= set(choices)

    async def recipients_of(audience, *picks):
        client.sent.clear()
        await bot.handle_update(cb(ADMIN, "bc:new"))
        await bot.handle_update(msg(ADMIN, f"Для {audience}"))
        for payload in picks:
            await bot.handle_update(cb(ADMIN, payload))
        preview = client.answers[-1]["text"]
        await bot.handle_update(cb(ADMIN, "bc:send"))
        return preview, sorted(m[0] for m in announcements(client))

    preview, sent = await recipients_of("students", "bc:aud:students")
    assert "все ученики, 3 чел." in preview and sent == [5, 6, 8]
    preview, sent = await recipients_of("teachers", "bc:aud:teachers")
    assert "все учителя, 1 чел." in preview and sent == [7]
    preview, sent = await recipients_of("moderators", "bc:aud:moderators")
    assert "модераторы, 1 чел." in preview and sent == [30]
    assert "Кому: модераторам" in preview and "Кому: модераторам" in announcements(client)[0][1]
    # ученики класса — без учителей класса
    preview, sent = await recipients_of("class", "bc:aud:cls", f"bc:aud:class:{other}")
    assert "ученики класса «5Б», 2 чел." in preview and sent == [6, 8]

    # одному ученику: класс → список учеников по алфавиту → ученик
    client.sent.clear()
    await bot.handle_update(cb(ADMIN, "bc:new"))
    await bot.handle_update(msg(ADMIN, "Лично тебе"))
    await bot.handle_update(cb(ADMIN, "bc:aud:pick"))
    assert f"bc:aud:pc:{other}" in payloads_of(client.answers[-1])
    await bot.handle_update(cb(ADMIN, f"bc:aud:pc:{other}"))
    names = [b["text"] for b in buttons(client.answers[-1]["attachments"])][:2]
    assert names == ["Андреев Андрей", "Сидоров Пётр"]
    await bot.handle_update(cb(ADMIN, f"bc:aud:user:{student.id}"))
    assert "ученик Андреев Андрей (5Б), 1 чел." in client.answers[-1]["text"]
    await bot.handle_update(cb(ADMIN, "bc:send"))
    assert [m[0] for m in announcements(client)] == [8]
    assert "От: Админов Админ (админ)\nКому: лично вам" in announcements(client)[0][1]
    # в истории — кому ушло
    async with sessions() as s:
        last = await s.scalar(select(Broadcast).order_by(Broadcast.id.desc()))
    await bot.handle_update(cb(ADMIN, f"bc:open:{last.id}"))
    assert "Получатели: ученик Андреев Андрей (5Б), 1 чел." in client.sent[-1][1]

    # лично учителю и модератору: список по роли → человек
    teacher, moderator = await get_user(sessions, 7), await get_user(sessions, 30)
    preview, sent = await recipients_of(
        "teacher", "bc:aud:pr:teacher:0", f"bc:aud:user:{teacher.id}"
    )
    assert "Получатели: учитель Учителева Анна, 1 чел." in preview and sent == [7]
    preview, sent = await recipients_of(
        "moderator", "bc:aud:pr:moderator:0", f"bc:aud:user:{moderator.id}"
    )
    assert "Получатели: модератор Модеров Максим, 1 чел." in preview and sent == [30]
    assert "Кому: лично вам" in announcements(client)[0][1]

    # в списке модераторов — только модераторы
    await bot.handle_update(cb(ADMIN, "bc:new"))
    await bot.handle_update(msg(ADMIN, "Проверка"))
    await bot.handle_update(cb(ADMIN, "bc:aud:pr:moderator:0"))
    picked = [b["payload"] for b in buttons(client.answers[-1]["attachments"])]
    assert f"bc:aud:user:{moderator.id}" in picked and f"bc:aud:user:{teacher.id}" not in picked
    # админу лично не написать
    admin = await get_user(sessions, ADMIN)
    await bot.handle_update(cb(ADMIN, f"bc:aud:user:{admin.id}"))
    assert "Получатель не найден" in client.answers[-1]["notification"]


VOICE = [{"type": "audio", "payload": {"token": "voice-tok", "url": "https://x/v.ogg"}}]
VOICE_OUT = {"type": "audio", "payload": {"token": "voice-tok"}}


async def test_voice_broadcast(env):
    """Голосовое вместо текста: доходит до получателей и остаётся после «Прочитано»."""
    bot, client, sessions = env
    await school_with_two_classes(bot, sessions)
    await bot.handle_update(cb(ADMIN, "bc:new"))
    await bot.handle_update(msg(ADMIN, "", attachments=VOICE))
    assert "Кому отправить" in client.sent[-1][1]
    await bot.handle_update(cb(ADMIN, "bc:aud:students"))
    assert VOICE_OUT in client.answers[-1]["attachments"]  # автор слышит запись до отправки
    await bot.handle_update(cb(ADMIN, "bc:send"))
    sent = announcements(client)
    assert sorted(m[0] for m in sent) == [5, 6]
    assert sent[0][1] == "📣 Оповещение\nОт: Админов Админ (админ)\nКому: всем ученикам"
    assert sent[0][2][0] == VOICE_OUT
    await bot.handle_update(cb(5, buttons(sent[0][2][1:])[0]["payload"]))
    assert client.answers[-1]["attachments"] == [VOICE_OUT]

    # модератор: голосовое уходит админу на подтверждение и затем получателям
    await register(bot, 30, "Модеров Максим")
    await bot.handle_update(msg(ADMIN, "/role 30 moderator"))
    await bot.handle_update(cb(30, "bc:new"))
    await bot.handle_update(msg(30, "Послушайте", attachments=VOICE))
    await bot.handle_update(cb(30, "bc:aud:teachers"))
    await bot.handle_update(cb(30, "bc:send"))
    to_admin = next(m for m in reversed(client.sent) if m[0] == ADMIN)
    assert VOICE_OUT in to_admin[2]
    async with sessions() as s:
        draft = await s.scalar(select(BroadcastDraft))
    client.sent.clear()
    await bot.handle_update(cb(ADMIN, f"bcd:ok:{draft.id}"))
    sent = announcements(client)
    assert [m[0] for m in sent] == [7] and sent[0][2][0] == VOICE_OUT


async def test_moderator_draft_keeps_audience(env):
    bot, client, sessions = env
    other = await school_with_two_classes(bot, sessions)
    await register(bot, 30, "Модеров Максим")
    await bot.handle_update(msg(ADMIN, "/role 30 moderator"))
    await bot.handle_update(cb(30, "bc:new"))
    await bot.handle_update(msg(30, "Для 5Б"))
    await bot.handle_update(cb(30, f"bc:aud:class:{other}"))
    await bot.handle_update(cb(30, "bc:send"))
    async with sessions() as s:
        draft = await s.scalar(select(BroadcastDraft))
    assert "(ученики класса «5Б»)" in next(m for m in reversed(client.sent) if m[0] == ADMIN)[1]
    client.sent.clear()
    await bot.handle_update(cb(ADMIN, f"bcd:ok:{draft.id}"))
    sent = announcements(client)
    assert [m[0] for m in sent] == [6]
    assert "От: Модеров Максим (модератор)\nКому: ученикам класса «5Б»" in sent[0][1]


async def test_only_admin_broadcasts(env):
    bot, client, sessions = env
    await school_with_two_classes(bot, sessions)
    for uid in (7, 5):
        await bot.handle_update(cb(uid, "bc:new"))
        assert "только админам" in client.answers[-1]["notification"]
        assert (await get_user(sessions, uid)).state is None
    await bot.handle_update(msg(7, "/start"))
    assert "menu:broadcast" not in [b["payload"] for b in buttons(client.sent[-1][2])]


async def test_broadcast_history(env):
    bot, client, sessions = env
    await school_with_two_classes(bot, sessions)
    for text in ("Первое", "Второе"):
        await bot.handle_update(cb(ADMIN, "bc:new"))
        await bot.handle_update(msg(ADMIN, text))
        await bot.handle_update(cb(ADMIN, "bc:aud:all"))
        await bot.handle_update(cb(ADMIN, "bc:send"))

    await bot.handle_update(cb(ADMIN, "bc:history:0"))
    answer = client.answers[-1]
    opened = [b for b in buttons(answer["attachments"]) if b["payload"].startswith("bc:open:")]
    assert [b["text"] for b in opened] == ["Второе", "Первое"]

    await bot.handle_update(cb(ADMIN, opened[1]["payload"]))
    to, text, _ = client.sent[-1]
    assert to == ADMIN and "Первое" in text
    assert "вся школа (ученики и учителя), 3 чел." in text and "Автор: Админов Админ" in text


async def test_broadcast_read_button(env):
    bot, client, sessions = env
    await school_with_two_classes(bot, sessions)
    await bot.handle_update(cb(ADMIN, "bc:new"))
    await bot.handle_update(msg(ADMIN, "Линейка"))
    await bot.handle_update(cb(ADMIN, "bc:aud:all"))
    await bot.handle_update(cb(ADMIN, "bc:send"))
    to_teacher = next(m for m in announcements(client) if m[0] == 7)
    read = buttons(to_teacher[2])[0]
    assert read["text"] == "✅ Прочитано"

    deleted_before = list(client.deleted)
    await bot.handle_update(cb(7, read["payload"]))
    answer = client.answers[-1]
    assert answer["text"] == (
        "📣 Оповещение\nОт: Админов Админ (админ)\nКому: всем ученикам и учителям\n\nЛинейка"
    )
    assert answer["attachments"] == []  # кнопка убрана, сообщение осталось
    assert client.deleted == deleted_before
    assert client.sent[-1][0] == 7 and "Ваша роль: учитель" in client.sent[-1][1]

    # чужое или несуществующее оповещение: просто меню
    await bot.handle_update(cb(5, "bc:read:999"))
    assert client.answers[-1]["notification"] == "Готово"
    assert client.sent[-1][0] == 5 and "Выберите раздел" in client.sent[-1][1]


# --- полученные оповещения у ученика ---


async def send_broadcast(bot, text, audience="all"):
    await bot.handle_update(cb(ADMIN, "bc:new"))
    await bot.handle_update(msg(ADMIN, text))
    await bot.handle_update(cb(ADMIN, f"bc:aud:{audience}"))
    await bot.handle_update(cb(ADMIN, "bc:send"))


def inbox_buttons(answer):
    return [b for b in buttons(answer["attachments"]) if b["payload"].startswith("inbox:open:")]


async def test_student_inbox(env):
    bot, client, sessions = env
    other = await school_with_two_classes(bot, sessions)

    await bot.handle_update(msg(5, "/start"))
    assert "inbox:0" in [b["payload"] for b in buttons(client.sent[-1][2])]
    await bot.handle_update(cb(5, "inbox:0"))
    assert "пока не было" in client.answers[-1]["text"]

    await send_broadcast(bot, "Всем привет")
    await send_broadcast(bot, "Только для 5Б", audience=f"class:{other}")
    await send_broadcast(bot, "Каникулы")

    await bot.handle_update(cb(5, "inbox:0"))
    answer = client.answers[-1]
    assert "Непрочитанных: 2" in answer["text"]
    items = inbox_buttons(answer)
    assert len(items) == 2  # оповещение для 5Б сюда не пришло
    assert items[0]["text"].startswith("🔵 ") and "Каникулы" in items[0]["text"]
    assert "Всем привет" in items[1]["text"]

    # открытие отмечает прочитанным
    await bot.handle_update(cb(5, items[1]["payload"]))
    to, text, att = client.sent[-1]
    assert to == 5 and text.startswith("📣 Оповещение") and "Всем привет" in text
    assert "Получено:" in text
    assert "inbox:0" in [b["payload"] for b in buttons(att)]

    # «Прочитано» под оповещением тоже отмечает
    read = next(
        b["payload"] for m in announcements(client) if m[0] == 5 and "Каникулы" in m[1]
        for b in buttons(m[2])
    )
    await bot.handle_update(cb(5, read))
    await bot.handle_update(cb(5, "inbox:0"))
    answer = client.answers[-1]
    assert "Непрочитанных" not in answer["text"]
    assert not any(b["text"].startswith("🔵") for b in inbox_buttons(answer))


async def test_inbox_only_received(env):
    bot, client, sessions = env
    await school_with_two_classes(bot, sessions)
    await send_broadcast(bot, "Раннее")
    await register(bot, 8, "Новенький Ученик")  # пришёл после рассылки
    await send_broadcast(bot, "Позднее")

    await bot.handle_update(cb(8, "inbox:0"))
    texts = [b["text"] for b in inbox_buttons(client.answers[-1])]
    assert len(texts) == 1 and "Позднее" in texts[0]

    # чужое оповещение не открыть
    async with sessions() as s:
        from app.db.models import Broadcast

        early = await s.scalar(select(Broadcast).where(Broadcast.text == "Раннее"))
    await bot.handle_update(cb(8, f"inbox:open:{early.id}"))
    assert "не найдено" in client.answers[-1]["notification"]


async def test_inbox_pages(env):
    bot, client, sessions = env
    await register(bot, ADMIN, "Админов Админ")
    await register(bot, 5)
    for i in range(12):
        await send_broadcast(bot, f"Новость {i:02}")
    await bot.handle_update(cb(5, "inbox:0"))
    page0 = client.answers[-1]
    assert len(inbox_buttons(page0)) == 10
    assert "inbox:1" in [b["payload"] for b in buttons(page0["attachments"])]
    await bot.handle_update(cb(5, "inbox:1"))
    page1 = client.answers[-1]
    assert [b["text"].split(" · ")[1] for b in inbox_buttons(page1)] == ["Новость 01", "Новость 00"]


# --- автор квеста ---


async def test_quest_shows_author(env):
    bot, client, sessions = env
    await make_teacher(bot, 7)
    await register(bot, 5)
    await create_quest(bot, 7, title="От учителя")
    await create_quest(bot, ADMIN, title="От админа")

    notices = {m[1].split("«")[1].split("»")[0]: m[1] for m in client.sent
               if m[0] == 5 and m[1].startswith("🆕 Новый квест")}
    assert "Автор: Учителева Анна, учитель" in notices["От учителя"]
    assert "Автор: Админов Админ, админ" in notices["От админа"]

    for title, author in (("От учителя", "Учителева Анна, учитель"), ("От админа", "Админов Админ, админ")):
        await bot.handle_update(cb(5, f"q:open:{await quest_id(sessions, title)}"))
        assert f"Автор: {author}" in client.answers[-1]["text"]


# --- монеты за прочтение оповещений ---


async def test_read_reward(env):
    bot, client, sessions = env
    await school_with_two_classes(bot, sessions)
    await send_broadcast(bot, "Первое")
    await send_broadcast(bot, "Второе")
    first = next(m for m in announcements(client) if m[0] == 5 and "Первое" in m[1])

    # «Прочитано»: +5, в меню видно начисление и новый баланс
    await bot.handle_update(cb(5, buttons(first[2])[0]["payload"]))
    to, text, _ = client.sent[-1]
    assert to == 5 and "+5 монет за прочтение" in text and "Баланс: 5 монет" in text
    assert (await get_user(sessions, 5)).balance == 5

    # повторное нажатие ничего не даёт
    await bot.handle_update(cb(5, buttons(first[2])[0]["payload"]))
    assert "за прочтение" not in client.sent[-1][1]
    assert (await get_user(sessions, 5)).balance == 5

    # открытие из истории — тоже первое прочтение, тоже +5, но один раз
    await bot.handle_update(cb(5, "inbox:0"))
    second = next(b for b in inbox_buttons(client.answers[-1]) if "Второе" in b["text"])
    await bot.handle_update(cb(5, second["payload"]))
    assert "+5 монет за прочтение. Баланс: 10 монет" in client.sent[-1][1]
    await bot.handle_update(cb(5, second["payload"]))
    assert "за прочтение" not in client.sent[-1][1]
    assert (await get_user(sessions, 5)).balance == 10

    # учителю монеты не начисляются
    to_teacher = next(m for m in announcements(client) if m[0] == 7)
    await bot.handle_update(cb(7, buttons(to_teacher[2])[0]["payload"]))
    assert (await get_user(sessions, 7)).balance == 0

    # начисления видны в истории монет ученика
    student = await get_user(sessions, 5)
    await bot.handle_update(cb(ADMIN, f"coins:st:{student.id}"))
    assert client.answers[-1]["text"].count("+5 · ") == 2
    assert "Прочитано оповещение" in client.answers[-1]["text"]


# --- картинка квеста ---


async def test_quest_photo_shown(env):
    bot, client, sessions = env
    await register(bot, ADMIN, "Админов Админ")
    await register(bot, 5)
    async with sessions() as s:
        from app.services import quests as quests_service

        admin = await s.scalar(select(User).where(User.max_user_id == ADMIN))
        quest = await quests_service.create_quest(
            s, admin, "С картинкой", "Описание", 100, QuestKind.photo, None, {"token": "pic"}
        )
    image = {"type": "image", "payload": {"token": "pic"}}

    await bot.handle_update(cb(5, f"q:open:{quest.id}"))
    answer = client.answers[-1]
    assert answer["attachments"][0] == image
    assert answer["attachments"][1]["type"] == "inline_keyboard"

    await bot.handle_update(cb(ADMIN, f"mq:open:{quest.id}"))
    assert client.answers[-1]["attachments"][0] == image

    # уведомление о новом квесте с картинкой (создание через бота, без картинки — без неё)
    await create_quest(bot, ADMIN, title="Без картинки")
    notice = next(m for m in client.sent if m[0] == 5 and "Без картинки" in m[1])
    assert notice[2][0]["type"] == "inline_keyboard"


# --- ответ на квест текстом ---


async def submit_text(bot, sessions, student, text, title="Убрать класс", attachments=None):
    qid = await quest_id(sessions, title)
    await bot.handle_update(cb(student, f"q:photo:{qid}"))
    await bot.handle_update(msg(student, text, attachments=attachments))
    async with sessions() as s:
        return await s.scalar(select(Submission).order_by(Submission.id.desc()))


async def test_text_answer(env):
    bot, client, sessions = env
    await make_teacher(bot, 7)
    await create_quest(bot, 7, kind="photo")
    await register(bot, 5)

    qid = await quest_id(sessions)
    await bot.handle_update(cb(5, f"q:open:{qid}"))
    assert "📝 Отправить ответ" in [b["text"] for b in buttons(client.answers[-1]["attachments"])]
    await bot.handle_update(cb(5, f"q:photo:{qid}"))
    assert "текстом, фото или видео" in client.answers[-1]["text"]
    await bot.handle_update(msg(5, "Мы с классом посадили три дерева у школы."))
    assert "Ответ отправлен на проверку" in client.sent[-1][1]
    async with sessions() as s:
        sub = await s.scalar(select(Submission))
    assert (sub.text, sub.photo) == ("Мы с классом посадили три дерева у школы.", None)

    # проверяющий видит текст ответа, без фото
    await bot.handle_update(cb(7, f"rev:open:{sub.id}"))
    to, text, att = client.sent[-1]
    assert to == 7 and "📝 Ответ:\nМы с классом посадили три дерева у школы." in text
    assert att[0]["type"] == "inline_keyboard"

    await bot.handle_update(cb(7, f"rev:ok:{sub.id}"))
    answer = client.answers[-1]
    assert "Мы с классом посадили" in answer["text"] and "Подтверждено" in answer["text"]
    assert answer["attachments"][0]["type"] == "inline_keyboard"
    assert (await get_user(sessions, 5)).balance == 50

    # в архиве ответ есть и открывается с текстом
    await bot.handle_update(cb(7, "arch:page:0"))
    assert f"arch:open:{sub.id}" in [b["payload"] for b in buttons(client.answers[-1]["attachments"])]
    await bot.handle_update(cb(7, f"arch:open:{sub.id}"))
    assert "Мы с классом посадили" in client.sent[-1][1]


async def test_photo_with_caption(env):
    bot, client, sessions = env
    await make_teacher(bot, 7)
    await create_quest(bot, 7, kind="photo")
    await register(bot, 5)
    sub = await submit_text(bot, sessions, 5, "Вот мой стол", attachments=PHOTO)
    assert (sub.text, sub.photo["token"]) == ("Вот мой стол", "tok")
    await bot.handle_update(cb(7, f"rev:open:{sub.id}"))
    _, text, att = client.sent[-1]
    assert "📝 Ответ:\nВот мой стол" in text
    assert att[0] == {"type": "image", "payload": {"token": "tok"}}


VIDEO = [{"type": "video", "payload": {"token": "vtok", "url": "https://x/v.mp4"}}]


async def test_video_answer(env):
    bot, client, sessions = env
    await make_teacher(bot, 7)
    await create_quest(bot, 7, kind="photo")
    await register(bot, 5)
    sub = await submit_text(bot, sessions, 5, "Отжался 10 раз", attachments=VIDEO)
    assert (sub.text, sub.photo, sub.video["token"]) == ("Отжался 10 раз", None, "vtok")

    await bot.handle_update(cb(7, f"rev:open:{sub.id}"))
    _, text, att = client.sent[-1]
    assert "📝 Ответ:\nОтжался 10 раз" in text and "🎬 Ответ с видео" in text
    assert att[0] == {"type": "video", "payload": {"token": "vtok"}}

    await bot.handle_update(cb(7, f"rev:ok:{sub.id}"))
    assert client.answers[-1]["attachments"][0]["type"] == "video"
    assert (await get_user(sessions, 5)).balance == 50

    await bot.handle_update(cb(7, f"arch:open:{sub.id}"))
    assert client.sent[-1][2][0]["type"] == "video"


async def test_video_only_answer(env):
    bot, client, sessions = env
    await make_teacher(bot, 7)
    await create_quest(bot, 7, kind="photo")
    await register(bot, 5)
    sub = await submit_text(bot, sessions, 5, "", attachments=VIDEO)
    assert (sub.text, sub.video["token"]) == (None, "vtok")
    assert "Ответ отправлен на проверку" in client.sent[-1][1]


async def test_too_long_text_answer(env):
    bot, client, sessions = env
    await make_teacher(bot, 7)
    await create_quest(bot, 7, kind="photo")
    await register(bot, 5)
    qid = await quest_id(sessions)
    await bot.handle_update(cb(5, f"q:photo:{qid}"))
    await bot.handle_update(msg(5, "а" * 3001))
    assert "Слишком длинный ответ" in client.sent[-1][1]
    assert (await get_user(sessions, 5)).state == "photo"  # можно прислать снова
    await bot.handle_update(msg(5, "а" * 3000))
    assert "Ответ отправлен" in client.sent[-1][1]


async def test_quests_sorted_by_reward(env):
    bot, client, sessions = env
    await make_teacher(bot, 7)
    await create_quest(bot, 7, title="Дорогой", reward="300")
    await create_quest(bot, 7, title="Дешёвый", reward="20")
    await create_quest(bot, 7, title="Средний", reward="100")
    await register(bot, 5)
    assert await visible_quests(bot, client, 5) == ["Дешёвый", "Средний", "Дорогой"]


async def test_reject_write_own_reason_button(env):
    bot, client, sessions = env
    await make_teacher(bot, 7)
    await create_quest(bot, 7, kind="photo")
    await register(bot, 5)
    sub_id = await submit(bot, sessions, 5)
    await bot.handle_update(cb(7, f"rev:no:{sub_id}"))
    keyboard_ = [a for a in client.answers[-1]["attachments"] if a["type"] == "inline_keyboard"]
    assert f"rev:nw:{sub_id}" in [b["payload"] for b in buttons(keyboard_)]
    await bot.handle_update(cb(7, f"rev:nw:{sub_id}"))
    assert "Напишите причину отклонения одним сообщением" in client.answers[-1]["text"]
    await bot.handle_update(msg(7, "Сфотографируйте стол целиком, пожалуйста"))
    note = next(text for to, text, _ in reversed(client.sent) if to == 5)
    assert "Причина: Сфотографируйте стол целиком, пожалуйста" in note
    async with sessions() as s:
        assert (await s.get(Submission, sub_id)).status == SubmissionStatus.rejected


async def test_daily_approved_only_in_admin_archive_section(env):
    """Засчитанные ответы ежедневных квестов — не в общем архиве, а в разделе админа."""
    bot, client, sessions = env
    await make_teacher(bot, 7)
    await create_quest(bot, 7, title="Зарядка", kind="photo", repeat="daily")
    await create_quest(bot, 7, title="Сочинение", kind="photo")
    await register(bot, 5)
    await register(bot, 6, "Петров Пётр")
    daily_ok = await submit_text(bot, sessions, 5, "Сделал", title="Зарядка")
    await bot.handle_update(cb(7, f"rev:ok:{daily_ok.id}"))
    daily_no = await submit_text(bot, sessions, 6, "Не сделал", title="Зарядка")
    await bot.handle_update(cb(7, f"rev:nr:{daily_no.id}:0"))
    essay = await submit_text(bot, sessions, 5, "Текст", title="Сочинение")
    await bot.handle_update(cb(7, f"rev:ok:{essay.id}"))

    def archive_payloads():
        return [b["payload"] for b in buttons(client.answers[-1]["attachments"])]

    # учитель: принятой зарядки нет, кнопки раздела нет, открыть по номеру нельзя
    await bot.handle_update(cb(7, "arch:page:0"))
    seen = archive_payloads()
    assert f"arch:open:{essay.id}" in seen and f"arch:open:{daily_no.id}" in seen
    assert f"arch:open:{daily_ok.id}" not in seen and "arch:daily:0" not in seen
    await bot.handle_update(cb(7, "arch:daily:0"))
    assert "только админу" in client.answers[-1]["notification"]
    await bot.handle_update(cb(7, f"arch:open:{daily_ok.id}"))
    assert "не найдена" in client.answers[-1]["notification"]

    # админ: в общем архиве тоже нет, но есть кнопка раздела
    await bot.handle_update(cb(ADMIN, "arch:page:0"))
    seen = archive_payloads()
    assert f"arch:open:{daily_ok.id}" not in seen and "arch:daily:0" in seen
    label = next(b["text"] for b in buttons(client.answers[-1]["attachments"]) if b["payload"] == "arch:daily:0")
    assert label == "✅ Принятые: Зарядка"
    await bot.handle_update(cb(ADMIN, "arch:daily:0"))
    seen = archive_payloads()
    assert f"arch:open:{daily_ok.id}" in seen
    assert f"arch:open:{essay.id}" not in seen and f"arch:open:{daily_no.id}" not in seen
    await bot.handle_update(cb(ADMIN, f"arch:open:{daily_ok.id}"))
    assert client.answers[-1]["notification"] == "Заявка открыта ниже"
