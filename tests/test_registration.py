"""Регистрация (роль → ФИО → класс / заявка учителя), классы и учителя (админ)."""
from sqlalchemy import select

from app.db.models import Role, SchoolClass, TeacherRequest, TeacherRequestStatus
from tests.test_bot import (  # noqa: F401  env — фикстура
    ADMIN,
    TEST_CLASS,
    assign_class,
    award,
    buttons,
    cb,
    class_id,
    create_quest,
    env,
    get_user,
    make_teacher,
    msg,
    register,
)


def texts(attachments) -> list[str]:
    return [b["text"] for b in buttons([a for a in attachments if a["type"] == "inline_keyboard"])]


def payloads(attachments) -> list[str]:
    return [b["payload"] for b in buttons([a for a in attachments if a["type"] == "inline_keyboard"])]


async def add_classes(sessions, *names):
    async with sessions() as s:
        s.add_all(SchoolClass(name=n) for n in names)
        await s.commit()


async def requests(sessions) -> list[TeacherRequest]:
    async with sessions() as s:
        return list((await s.scalars(select(TeacherRequest).order_by(TeacherRequest.id))).unique())


async def apply_as_teacher(bot, uid, name="Петрова Мария", classes=(TEST_CLASS,)):
    """Заявка учителя: роль → ФИО → отметить классы → отправить."""
    await bot.handle_update(msg(uid, "/start"))
    await bot.handle_update(cb(uid, "reg:role:teacher"))
    await bot.handle_update(cb(uid, "reg:next"))
    await bot.handle_update(cb(uid, "reg:teacher_ok"))
    await bot.handle_update(msg(uid, name))
    for class_name in classes:
        await bot.handle_update(cb(uid, f"reg:tcl:{await class_id(bot.sessions, class_name)}"))
    await bot.handle_update(cb(uid, "reg:tcl_done"))


# --- ученик ---


async def test_role_toggle(env):
    bot, client, _ = env
    await bot.handle_update(msg(5, "/start"))
    await bot.handle_update(cb(5, "reg:role:teacher"))
    assert "✅ 👩‍🏫 Учитель" in texts(client.answers[-1]["attachments"])
    await bot.handle_update(cb(5, "reg:role:student"))
    assert "✅ 🎒 Ученик" in texts(client.answers[-1]["attachments"])


async def test_student_picks_class_by_category(env):
    bot, client, sessions = env
    await add_classes(sessions, "3А", "3Б", "5Б", "10В")
    await register(bot, ADMIN, "Админов Админ")
    await make_teacher(bot, 7, school_class="3А")
    await make_teacher(bot, 8, name="Другая Учительница", school_class="5Б")
    sent_before = len(client.sent)

    await bot.handle_update(msg(5, "/start"))
    await bot.handle_update(cb(5, "reg:next"))
    assert "фамилию и имя" in client.answers[-1]["text"]
    await bot.handle_update(msg(5, "Сидоров Пётр"))
    _, text, att = client.sent[-1]
    assert "Сначала выберите группу" in text
    assert texts(att) == ["1–4 классы", "5–9 классы", "10–11 классы", "Другие", "« Назад"]

    await bot.handle_update(cb(5, "reg:cat:1-4"))
    answer = client.answers[-1]
    assert texts(answer["attachments"])[:2] == ["3А", "3Б"]
    cid = await class_id(sessions, "3А")
    await bot.handle_update(cb(5, f"reg:class:{cid}"))
    assert "Вы зачислены в класс 3А" in client.answers[-1]["text"]
    student = await get_user(sessions, 5)
    assert (student.full_name, student.class_id, student.role) == ("Сидоров Пётр", cid, Role.student)

    # уведомили только учителя 3А; админ видит зачисление в журнале
    notices = [m for m in client.sent[sent_before:] if m[1].startswith("🆕 Новый ученик")]
    assert [m[0] for m in notices] == [7]
    assert payloads(notices[0][2]) == [f"enr:del:{student.id}:{cid}", f"enr:mv:{student.id}:{cid}", "menu:main"]
    await bot.handle_update(cb(ADMIN, "enl:0"))
    assert "Сидоров Пётр · 3А" in " ".join(texts(client.answers[-1]["attachments"]))


async def test_no_classes_yet(env):
    bot, client, sessions = env
    async with sessions() as s:
        for c in await s.scalars(select(SchoolClass)):
            await s.delete(c)
        await s.commit()
    await bot.handle_update(msg(5, "/start"))
    await bot.handle_update(cb(5, "reg:next"))
    await bot.handle_update(msg(5, "Сидоров Пётр"))
    assert "Классы ещё не созданы" in client.sent[-1][1]
    await add_classes(sessions, "7А")
    await bot.handle_update(cb(5, "reg:cats"))
    assert "5–9 классы" in texts(client.answers[-1]["attachments"])


async def test_back_from_name_to_role(env):
    bot, client, sessions = env
    await bot.handle_update(msg(5, "/start"))
    await bot.handle_update(cb(5, "reg:next"))
    await bot.handle_update(cb(5, "reg:back"))
    assert "Кто вы?" in client.answers[-1]["text"]
    # без выбранного класса регистрация не завершена
    await bot.handle_update(cb(5, f"reg:class:{await class_id(sessions)}"))
    assert (await get_user(sessions, 5)).full_name is None


# --- учитель ---


async def test_teacher_application_flow(env):
    bot, client, sessions = env
    await add_classes(sessions, "5Б", "6А")
    await register(bot, ADMIN, "Админов Админ")

    await bot.handle_update(msg(9, "/start"))
    await bot.handle_update(cb(9, "reg:role:teacher"))
    await bot.handle_update(cb(9, "reg:next"))
    answer = client.answers[-1]
    assert "классы, в которых работаете" in answer["text"]
    assert payloads(answer["attachments"]) == ["reg:teacher_ok", "reg:back"]

    # «Назад» возвращает к выбору роли, учитель остаётся выбранным
    await bot.handle_update(cb(9, "reg:back"))
    assert "✅ 👩‍🏫 Учитель" in texts(client.answers[-1]["attachments"])
    await bot.handle_update(cb(9, "reg:next"))
    await bot.handle_update(cb(9, "reg:teacher_ok"))
    await bot.handle_update(msg(9, "петрова мария"))
    # учитель отмечает свои классы
    assert "Отметьте классы" in client.sent[-1][1]
    c5, c6 = await class_id(sessions, "5Б"), await class_id(sessions, "6А")
    await bot.handle_update(cb(9, "reg:tcl_done"))
    assert "хотя бы один класс" in client.answers[-1]["notification"]
    await bot.handle_update(cb(9, f"reg:tcl:{c5}"))
    await bot.handle_update(cb(9, f"reg:tcl:{c6}"))
    assert "Выбрано: 5Б, 6А" in client.answers[-1]["text"]
    await bot.handle_update(cb(9, "reg:tcl_done"))
    assert "Заявка отправлена" in client.answers[-1]["text"]
    [request] = await requests(sessions)
    assert (request.full_name, request.status) == ("Петрова Мария", TeacherRequestStatus.pending)
    assert sorted(request.class_ids) == sorted([c5, c6])
    user = await get_user(sessions, 9)
    assert user.full_name is None and user.role == Role.student

    notice = next(m for m in client.sent if m[0] == ADMIN and "Заявка на роль учителя" in m[1])
    assert "Классы, которые выбрал учитель: 5Б, 6А" in notice[1]
    assert payloads(notice[2]) == [f"tr:ok:{request.id}", f"tr:no:{request.id}", f"tr:edit:{request.id}", "menu:main"]

    # пока ждёт — только статус заявки
    await bot.handle_update(msg(9, "/start"))
    assert "на рассмотрении" in client.sent[-1][1]
    await bot.handle_update(cb(9, "menu:quests"))
    assert "на рассмотрении" in client.answers[-1]["text"]

    # админ правит выбор (убирает 6А) и подтверждает
    await bot.handle_update(cb(ADMIN, f"tr:edit:{request.id}"))
    assert "Сейчас: 5Б, 6А" in client.answers[-1]["text"]
    await bot.handle_update(cb(ADMIN, f"tr:t:{request.id}:{c6}"))
    assert "Сейчас: 5Б" in client.answers[-1]["text"]
    await bot.handle_update(cb(ADMIN, f"tr:ok:{request.id}"))
    editor = client.answers[-1]
    assert "Роль подтверждена, классы назначены: 5Б" in editor["text"]
    teacher = await get_user(sessions, 9)
    assert (teacher.role, teacher.full_name) == (Role.teacher, "Петрова Мария")
    assert client.sent[-1][0] == 9
    assert "теперь вы учитель" in client.sent[-1][1] and "Ваши классы: 5Б" in client.sent[-1][1]

    await bot.handle_update(msg(9, "/start"))
    assert "Ваша роль: учитель" in client.sent[-1][1] and "Классы: 5Б" in client.sent[-1][1]

    # повторно заявку не рассмотреть
    await bot.handle_update(cb(ADMIN, f"tr:no:{request.id}"))
    assert "уже рассмотрена" in client.answers[-1]["notification"]


async def test_teacher_application_rejected(env):
    bot, client, sessions = env
    await register(bot, ADMIN, "Админов Админ")
    await apply_as_teacher(bot, 9)
    [request] = await requests(sessions)
    await bot.handle_update(cb(ADMIN, f"tr:no:{request.id}"))
    assert "отклонена" in client.answers[-1]["text"]
    to, text, att = client.sent[-1]
    assert to == 9 and "отклонена" in text and payloads(att) == ["reg:restart"]
    assert (await get_user(sessions, 9)).role == Role.student

    # можно зарегистрироваться заново, например учеником
    await bot.handle_update(cb(9, "reg:restart"))
    assert "Кто вы?" in client.answers[-1]["text"]
    await bot.handle_update(cb(9, "reg:next"))
    await bot.handle_update(msg(9, "Петрова Мария"))
    await bot.handle_update(cb(9, f"reg:class:{await class_id(sessions)}"))
    assert (await get_user(sessions, 9)).full_name == "Петрова Мария"


async def test_teacher_application_withdrawn(env):
    bot, client, sessions = env
    await register(bot, ADMIN, "Админов Админ")
    await apply_as_teacher(bot, 9)
    await bot.handle_update(cb(9, "reg:withdraw"))
    assert "Кто вы?" in client.answers[-1]["text"]
    [request] = await requests(sessions)
    assert request.status == TeacherRequestStatus.withdrawn
    await bot.handle_update(cb(ADMIN, f"tr:ok:{request.id}"))
    assert "уже рассмотрена или отозвана" in client.answers[-1]["notification"]
    assert (await get_user(sessions, 9)).role == Role.student


async def test_teachers_overview(env):
    bot, client, sessions = env
    await make_teacher(bot, 7)
    await apply_as_teacher(bot, 9)
    await bot.handle_update(cb(ADMIN, "menu:teachers"))
    answer = client.answers[-1]
    assert "Ждут подтверждения: 1" in answer["text"]
    labels = texts(answer["attachments"])
    assert "⏳ Петрова Мария" in labels
    assert "Учителева Анна · Тестовый класс" in labels


# --- проверка зачисления ---


async def test_teacher_removes_enrolled_student(env):
    bot, client, sessions = env
    await make_teacher(bot, 7)
    await register(bot, 5)
    await award(sessions, 5, 50)
    student = await get_user(sessions, 5)
    cid = await class_id(sessions)

    await bot.handle_update(cb(7, f"enr:del:{student.id}:{cid}"))
    assert "Удалить ученика Иванов Иван" in client.answers[-1]["text"]
    assert (await get_user(sessions, 5)).full_name is not None  # без подтверждения
    await bot.handle_update(cb(7, f"enr:del_ok:{student.id}:{cid}"))
    assert "Иванов Иван отменена" in client.answers[-1]["text"]
    student = await get_user(sessions, 5)
    assert (student.full_name, student.class_id, student.balance) == (None, None, 0)
    to, text, att = client.sent[-1]
    assert to == 5 and "регистрация отменена" in text and payloads(att) == ["reg:restart"]

    await bot.handle_update(cb(7, f"enr:del_ok:{student.id}:{cid}"))
    assert "уже удалён" in client.answers[-1]["notification"]


async def test_teacher_asks_admin_to_transfer(env):
    bot, client, sessions = env
    await make_teacher(bot, 7)
    await register(bot, 5)
    student = await get_user(sessions, 5)
    await bot.handle_update(cb(7, f"enr:mv:{student.id}:{await class_id(sessions)}"))
    assert "отправлен администратору" in client.answers[-1]["notification"]
    to, text, att = client.sent[-1]
    assert to == ADMIN and "просит перевести ученика Иванов Иван" in text
    assert payloads(att) == [f"cls:mv:{student.id}", f"cls:del:{student.id}", "menu:main"]


async def test_other_teacher_cannot_remove(env):
    bot, client, sessions = env
    await add_classes(sessions, "5Б")
    await make_teacher(bot, 7)
    await make_teacher(bot, 8, name="Другая Учительница", school_class="5Б")
    await register(bot, 5)
    student = await get_user(sessions, 5)
    await bot.handle_update(cb(8, f"enr:del_ok:{student.id}:{await class_id(sessions)}"))
    assert "не вашего класса" in client.answers[-1]["notification"]
    await bot.handle_update(cb(5, f"enr:del_ok:{student.id}:{await class_id(sessions)}"))
    assert "не вашего класса" in client.answers[-1]["notification"]
    assert (await get_user(sessions, 5)).full_name == "Иванов Иван"


async def test_admin_transfers_student(env):
    bot, client, sessions = env
    await add_classes(sessions, "6А", "9А")
    await register(bot, ADMIN, "Админов Админ")
    await register(bot, 5, school_class="9А")
    student = await get_user(sessions, 5)

    await bot.handle_update(cb(ADMIN, f"cls:mv:{student.id}"))
    picker = payloads(client.answers[-1]["attachments"])
    c6, c9 = await class_id(sessions, "6А"), await class_id(sessions, "9А")
    assert f"cls:mvto:{student.id}:{c6}" in picker
    assert f"cls:mvto:{student.id}:{c9}" not in picker  # текущий класс

    await bot.handle_update(cb(ADMIN, f"cls:mvto:{student.id}:{c6}"))
    assert "Переведён в 6А" in client.answers[-1]["text"]
    assert (await get_user(sessions, 5)).class_id == c6
    assert client.sent[-1][0] == 5 and "переведены в класс 6А" in client.sent[-1][1]


# --- классы (админ) ---


async def test_admin_adds_and_manages_classes(env):
    bot, client, sessions = env
    await register(bot, ADMIN, "Админов Админ")
    await bot.handle_update(cb(ADMIN, "menu:classes"))
    assert "Классы: 1" in client.answers[-1]["text"]

    await bot.handle_update(cb(ADMIN, "cls:add"))
    await bot.handle_update(msg(ADMIN, "5а 12Б"))
    assert "«12Б» — не похоже на класс" in client.sent[-1][1]
    await bot.handle_update(msg(ADMIN, "5а, 5 Б 6-в 1г"))
    text = client.sent[-1][1]
    assert "Добавлены: 5А, 5Б, 6В, 1Г" in text
    assert "1–4 классы: 1Г" in text and "5–9 классы: 5А, 5Б, 6В" in text and "Другие: Тестовый класс" in text

    await bot.handle_update(cb(ADMIN, "cls:add"))
    await bot.handle_update(msg(ADMIN, "5А 7Г"))
    assert "Добавлены: 7Г" in client.sent[-1][1] and "Уже были: 5А" in client.sent[-1][1]

    # переименовать «Тестовый класс» в настоящий
    test_cid = await class_id(sessions)
    await bot.handle_update(cb(ADMIN, f"cls:ren:{test_cid}"))
    await bot.handle_update(msg(ADMIN, "5А"))
    assert "Класс «5А» уже есть" in client.sent[-1][1]
    await bot.handle_update(msg(ADMIN, "8б"))
    assert "называется 8Б" in client.sent[-1][1]

    # пустой класс удаляется, класс с учениками — нет
    await register(bot, 5, school_class="8Б")
    await bot.handle_update(cb(ADMIN, f"cls:rm_ok:{test_cid}"))
    assert "есть ученики" in client.answers[-1]["notification"]
    c7 = await class_id(sessions, "7Г")
    await bot.handle_update(cb(ADMIN, f"cls:rm:{c7}"))
    await bot.handle_update(cb(ADMIN, f"cls:rm_ok:{c7}"))
    assert "Класс 7Г удалён" in client.answers[-1]["text"]

    await bot.handle_update(cb(ADMIN, f"cls:open:{test_cid}"))
    assert "Учеников: 1" in client.answers[-1]["text"]
    await bot.handle_update(cb(ADMIN, f"cls:st:{test_cid}:0"))
    student = await get_user(sessions, 5)
    assert f"cls:stu:{student.id}" in payloads(client.answers[-1]["attachments"])


async def test_only_admin_manages_school(env):
    bot, client, sessions = env
    await make_teacher(bot, 7)
    await register(bot, 5)
    for uid in (7, 5):
        for payload in ("cls:add", "menu:teachers", f"cls:del_ok:{(await get_user(sessions, 5)).id}"):
            await bot.handle_update(cb(uid, payload))
            assert "доступ" in client.answers[-1]["notification"].lower()
    assert (await get_user(sessions, 5)).full_name == "Иванов Иван"


# --- учитель нескольких классов ---


async def test_teacher_with_several_classes(env):
    bot, client, sessions = env
    await add_classes(sessions, "5Б", "6А")
    await make_teacher(bot, 7)
    await assign_class(bot, 7, "5Б")
    await register(bot, 5)
    await register(bot, 6, "Сидоров Пётр", school_class="5Б")
    await register(bot, 4, "Ученик Чужой", school_class="6А")

    await bot.handle_update(msg(7, "/start"))
    assert "Классы: 5Б, Тестовый класс" in client.sent[-1][1]

    # квест: шестой шаг — выбор из своих классов, без «Вся школа»
    await bot.handle_update(cb(7, "menu:new_quest"))
    assert "Шаг 1 из 7" in client.answers[-1]["text"]
    await bot.handle_update(msg(7, "Квест для 5Б"))
    await bot.handle_update(msg(7, "Описание"))
    await bot.handle_update(msg(7, "10"))
    await bot.handle_update(cb(7, "nq:kind:auto"))
    await bot.handle_update(cb(7, "nq:rep:none"))
    options = payloads(client.answers[-1]["attachments"])
    c5 = await class_id(sessions, "5Б")
    assert f"nq:aud:{c5}" in options and "nq:aud:all" not in options
    assert f"nq:aud:{await class_id(sessions, '6А')}" not in options
    await bot.handle_update(cb(7, f"nq:aud:{c5}"))
    await bot.handle_update(cb(7, "nq:photo:none"))
    assert "ученикам класса «5Б»" in client.answers[-1]["text"]

    # монеты: сначала выбор класса
    await bot.handle_update(cb(7, "menu:coins"))
    assert "Выберите класс" in client.answers[-1]["text"]
    s6 = (await get_user(sessions, 6)).id
    s4 = (await get_user(sessions, 4)).id
    await bot.handle_update(cb(7, f"coins:st:{s6}"))
    assert "Сидоров Пётр" in client.answers[-1]["text"]
    await bot.handle_update(cb(7, f"coins:st:{s4}"))
    assert "не найден" in client.answers[-1]["notification"]


# --- журнал регистраций и статистика ---


async def test_enrollment_log(env):
    bot, client, sessions = env
    await add_classes(sessions, "5Б")
    await register(bot, ADMIN, "Админов Админ")
    await register(bot, 5)
    await register(bot, 6, "Сидоров Пётр", school_class="5Б")
    assert not [m for m in client.sent if m[0] == ADMIN and "Новый ученик" in m[1]]

    await bot.handle_update(cb(ADMIN, "enl:0"))
    answer = client.answers[-1]
    assert "Не проверено: 2" in answer["text"]
    labels = texts(answer["attachments"])
    assert labels[0] == "✅ Отметить все проверенными"
    assert labels[1].startswith("🔵 ") and labels[1].endswith("Сидоров Пётр · 5Б")  # новые сверху
    assert labels[2].endswith("Иванов Иван · Тестовый класс")

    # открытие отмечает проверенным и показывает карточку с переводом и удалением
    entry = next(p for p in payloads(answer["attachments"]) if p.startswith("enl:open:"))
    await bot.handle_update(cb(ADMIN, entry))
    card = client.answers[-1]
    assert "Регистрация" in card["text"] and "Сидоров Пётр" in card["text"]
    student = await get_user(sessions, 6)
    assert f"cls:mv:{student.id}" in payloads(card["attachments"])
    await bot.handle_update(cb(ADMIN, "enl:0"))
    assert "Не проверено: 1" in client.answers[-1]["text"]

    await bot.handle_update(cb(ADMIN, "enl:all"))
    answer = client.answers[-1]
    assert "Отмечено проверенными: 1" in answer["text"] and "Все проверены" in answer["text"]
    assert "✅ Отметить все проверенными" not in texts(answer["attachments"])

    # удалённый ученик остаётся в журнале
    await bot.handle_update(cb(ADMIN, f"cls:del_ok:{student.id}"))
    await bot.handle_update(cb(ADMIN, entry))
    assert "уже удалён" in client.answers[-1]["text"]

    await bot.handle_update(cb(5, "enl:0"))
    assert "доступ" in client.answers[-1]["notification"].lower()


async def test_stats(env):
    bot, client, sessions = env
    await add_classes(sessions, "3А", "5Б", "10В")
    await make_teacher(bot, 7, school_class="5Б")
    await register(bot, 8, "Без Классов")
    await bot.handle_update(msg(ADMIN, "/role 8 teacher"))
    await register(bot, 5, school_class="5Б")
    await register(bot, 6, "Сидоров Пётр", school_class="5Б")
    await register(bot, 4, "Петров Иван", school_class="10В")
    await bot.handle_update(msg(9, "/start"))  # не закончил регистрацию
    await apply_as_teacher(bot, 3)

    await bot.handle_update(cb(ADMIN, "menu:stats"))
    text = client.answers[-1]["text"]
    for line in (
        "Пользователей: 8",
        "• учеников: 3",
        "• учителей: 2",
        "• админов: 1",
        "• не завершили регистрацию: 2",
        "⏳ Заявок на роль учителя: 1",
        "1–4 классы — 0",
        "5–9 классы — 2",
        "10–11 классы — 1",
        "  10В: 1",
        "• Учителева Анна — 5Б",
        "• Без Классов — классы не назначены",
        "Классы без учителя: 3А, 10В, Тестовый класс",
    ):
        assert line in text.splitlines(), line

    await bot.handle_update(cb(7, "menu:stats"))
    assert "доступ" in client.answers[-1]["notification"].lower()


async def test_admin_menu_in_two_columns(env):
    bot, client, _ = env
    await register(bot, ADMIN, "Админов Админ")
    await bot.handle_update(msg(ADMIN, "/start"))
    rows = client.sent[-1][2][0]["payload"]["buttons"]
    assert [len(r) for r in rows[:-1]] == [2] * (len(rows) - 1)
    labels = [b["text"] for r in rows for b in r]
    assert "🆕 Регистрации" in labels and "📊 Статистика" in labels
