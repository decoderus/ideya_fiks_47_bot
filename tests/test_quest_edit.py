"""Редактирование квестов: поля, права, картинка, периодичность."""
from sqlalchemy import select

from app.db.models import Achievement, AchievementKind, Quest, QuestRepeat, Submission
from tests.test_bot import (  # noqa: F401  env — фикстура
    ADMIN,
    PHOTO,
    assign_class,
    buttons,
    cb,
    create_quest,
    env,
    get_user,
    make_teacher,
    msg,
    quest_id,
    register,
    submit_text,
)


def payloads(answer) -> list[str]:
    attachments = [a for a in answer["attachments"] if a["type"] == "inline_keyboard"]
    return [b["payload"] for b in buttons(attachments)]


async def get_quest(sessions, qid) -> Quest:
    async with sessions() as s:
        return await s.get(Quest, qid)


async def test_teacher_edits_text_fields(env, fixed_ranges):
    bot, client, sessions = env
    await make_teacher(bot, 7)
    await create_quest(bot, 7)
    qid = await quest_id(sessions)

    await bot.handle_update(cb(7, f"mq:open:{qid}"))
    assert f"mq:edit:{qid}" in payloads(client.answers[-1])
    await bot.handle_update(cb(7, f"mq:edit:{qid}"))
    assert f"mq:ef:title:{qid}" in payloads(client.answers[-1])

    await bot.handle_update(cb(7, f"mq:ef:title:{qid}"))
    assert "Сейчас: Убрать класс" in client.answers[-1]["text"]
    await bot.handle_update(msg(7, "Генеральная уборка"))
    assert "Новое название: «Генеральная уборка»" in client.sent[-1][1]

    await bot.handle_update(cb(7, f"mq:ef:description:{qid}"))
    await bot.handle_update(msg(7, "Протереть парты и полить цветы"))
    await bot.handle_update(cb(7, f"mq:ef:reward:{qid}"))
    await bot.handle_update(msg(7, "151"))
    assert "от 50 до 150" in client.sent[-1][1]
    await bot.handle_update(msg(7, "80"))
    assert "Новая награда: 80 монет" in client.sent[-1][1]

    quest = await get_quest(sessions, qid)
    assert (quest.title, quest.description, quest.reward) == (
        "Генеральная уборка", "Протереть парты и полить цветы", 80
    )


async def test_new_reward_applies_to_pending(env):
    bot, client, sessions = env
    await make_teacher(bot, 7)
    await create_quest(bot, 7, kind="photo")
    await register(bot, 5)
    sub = await submit_text(bot, sessions, 5, "Готово")
    qid = await quest_id(sessions)
    await bot.handle_update(cb(7, f"mq:ef:reward:{qid}"))
    await bot.handle_update(msg(7, "120"))
    await bot.handle_update(cb(7, f"rev:ok:{sub.id}"))
    assert (await get_user(sessions, 5)).balance == 120


async def test_edit_repeat_and_photo(env):
    bot, client, sessions = env
    await make_teacher(bot, 7)
    await create_quest(bot, 7)
    qid = await quest_id(sessions)

    await bot.handle_update(cb(7, f"mq:ef:repeat:{qid}"))
    assert f"mq:rep:daily:{qid}" in payloads(client.answers[-1])
    await bot.handle_update(cb(7, f"mq:rep:daily:{qid}"))
    assert "Периодичность: раз в день" in client.answers[-1]["text"]
    assert (await get_quest(sessions, qid)).repeat == QuestRepeat.daily

    await bot.handle_update(cb(7, f"mq:ef:photo:{qid}"))
    assert f"mq:photo_rm:{qid}" not in payloads(client.answers[-1])  # картинки ещё нет
    await bot.handle_update(msg(7, "без картинки"))
    assert "как фото" in client.sent[-1][1]
    await bot.handle_update(msg(7, "", attachments=PHOTO))
    _, text, att = client.sent[-1]
    assert "Картинка обновлена" in text and att[0]["type"] == "image"
    assert (await get_quest(sessions, qid)).photo["token"] == "tok"

    await bot.handle_update(cb(7, f"mq:ef:photo:{qid}"))
    await bot.handle_update(cb(7, f"mq:photo_rm:{qid}"))
    assert "Картинка убрана" in client.answers[-1]["text"]
    assert (await get_quest(sessions, qid)).photo is None


async def test_edit_permissions(env):
    bot, client, sessions = env
    await make_teacher(bot, 7)
    await register(bot, 8, "Другая Учительница")
    await bot.handle_update(msg(ADMIN, "/role 8 teacher"))
    await assign_class(bot, 8)
    await create_quest(bot, 8, title="Чужой")
    await create_quest(bot, ADMIN, title="Админский")
    for title in ("Чужой", "Админский"):
        qid = await quest_id(sessions, title)
        for payload in (f"mq:edit:{qid}", f"mq:ef:title:{qid}", f"mq:rep:daily:{qid}"):
            await bot.handle_update(cb(7, payload))
            assert "не найден" in client.answers[-1]["notification"]
    # админ правит любой квест и без лимита награды
    qid = await quest_id(sessions, "Чужой")
    await bot.handle_update(cb(ADMIN, f"mq:ef:reward:{qid}"))
    await bot.handle_update(msg(ADMIN, "2000"))
    assert (await get_quest(sessions, qid)).reward == 2000


async def test_rename_updates_streaks(env):
    bot, client, sessions = env
    await make_teacher(bot, 7)
    await create_quest(bot, 7, title="Зарядка", repeat="daily")
    await register(bot, 5)
    await bot.handle_update(cb(5, "menu:achievements"))  # серии создаются
    qid = await quest_id(sessions, "Зарядка")
    await bot.handle_update(cb(7, f"mq:ef:title:{qid}"))
    await bot.handle_update(msg(7, "Утренняя зарядка"))
    async with sessions() as s:
        titles = sorted(
            a.title
            for a in await s.scalars(
                select(Achievement).where(Achievement.kind == AchievementKind.streak)
            )
        )
    assert titles == ["Утренняя зарядка: месяц подряд", "Утренняя зарядка: неделя подряд"]


# --- картинка при создании ---


async def test_create_quest_with_photo(env):
    bot, client, sessions = env
    await make_teacher(bot, 7)
    await register(bot, 5)
    await bot.handle_update(cb(7, "menu:new_quest"))
    for text in ("Порядок на столе", "Сфотографируйте стол", "40"):
        await bot.handle_update(msg(7, text))
    await bot.handle_update(cb(7, "nq:kind:photo"))
    await bot.handle_update(cb(7, "nq:rep:none"))
    assert "Шаг 6 из 6" in client.answers[-1]["text"]
    assert "nq:photo:none" in payloads(client.answers[-1])

    await bot.handle_update(msg(7, "вот"))  # текст вместо картинки
    assert "как фото" in client.sent[-1][1]
    async with sessions() as s:
        assert await s.scalar(select(Quest)) is None

    await bot.handle_update(msg(7, "", attachments=PHOTO))
    quest = await get_quest(sessions, await quest_id(sessions, "Порядок на столе"))
    assert quest.photo["token"] == "tok"
    to_teacher = [m for m in client.sent if m[0] == 7][-1]
    assert "Квест создан" in to_teacher[1] and to_teacher[2][0]["type"] == "image"
    # уведомление ученику — с картинкой
    to_student = [m for m in client.sent if m[0] == 5 and "Новый квест" in m[1]][-1]
    assert to_student[2][0] == {"type": "image", "payload": {"token": "tok"}}


async def test_create_quest_without_photo(env):
    bot, client, sessions = env
    await make_teacher(bot, 7)
    await create_quest(bot, 7)
    assert "Квест создан" in client.answers[-1]["text"]
    assert (await get_quest(sessions, await quest_id(sessions))).photo is None


# --- награда-диапазон ---


async def range_quest(bot, sessions):
    """Учитель 7 создаёт квест с проверкой и наградой 100–500, ученик 5 отвечает."""
    await make_teacher(bot, 7)
    await register(bot, 5)
    await bot.handle_update(cb(7, "menu:new_quest"))
    for text in ("Моё достижение", "Грамота или диплом", "100-500"):
        await bot.handle_update(msg(7, text))
    await bot.handle_update(cb(7, "nq:kind:photo"))
    await bot.handle_update(cb(7, "nq:rep:weekly"))
    await bot.handle_update(cb(7, "nq:photo:none"))
    qid = await quest_id(sessions, "Моё достижение")
    sub = await submit_text(bot, sessions, 5, "Диплом олимпиады", title="Моё достижение")
    return qid, sub


async def test_range_quest_reviewer_chooses_amount(env):
    bot, client, sessions = env
    qid, sub = await range_quest(bot, sessions)
    quest = await get_quest(sessions, qid)
    assert (quest.reward_min, quest.reward, quest.repeat) == (100, 500, QuestRepeat.weekly)

    await bot.handle_update(cb(5, "menu:quests"))
    labels = [b["text"] for b in buttons(client.answers[-1]["attachments"])]
    assert any("Моё достижение · 100–500" in t for t in labels)

    await bot.handle_update(cb(7, f"rev:open:{sub.id}"))
    _, text, att = client.sent[-1]
    assert "Награда: 100–500 монет" in text
    keys = [b["payload"] for row in att[-1]["payload"]["buttons"] for b in row]
    assert keys[:5] == [f"rev:ok:{sub.id}:{a}" for a in (100, 200, 300, 400, 500)]
    assert f"rev:no:{sub.id}" in keys

    # без суммы и вне диапазона — нельзя
    await bot.handle_update(cb(7, f"rev:ok:{sub.id}"))
    assert "Выберите сумму" in client.answers[-1]["notification"]
    await bot.handle_update(cb(7, f"rev:ok:{sub.id}:900"))
    assert "Выберите сумму" in client.answers[-1]["notification"]

    await bot.handle_update(cb(7, f"rev:ok:{sub.id}:300"))
    assert "начислено 300 монет" in client.answers[-1]["text"]
    assert (await get_user(sessions, 5)).balance == 300
    async with sessions() as s:
        assert (await s.get(Submission, sub.id)).reward == 300

    # заработок считается по начисленной сумме, а не по максимуму квеста
    student = await get_user(sessions, 5)
    await bot.handle_update(cb(7, f"jr:st:{student.id}"))
    assert "заработано: 300 монет" in client.answers[-1]["text"]


async def test_range_override_and_edit(env):
    bot, client, sessions = env
    qid, sub = await range_quest(bot, sessions)
    await bot.handle_update(cb(7, f"rev:ok:{sub.id}:200"))
    # админ отменяет — списывается именно 200
    await bot.handle_update(cb(ADMIN, f"rev:flip:{sub.id}"))
    assert "списано 200 монет" in client.answers[-1]["text"]
    assert (await get_user(sessions, 5)).balance == 0
    # и возвращает — снова 200
    await bot.handle_update(cb(ADMIN, f"rev:flip:{sub.id}"))
    assert (await get_user(sessions, 5)).balance == 200

    # смена награды квеста не меняет историю
    await bot.handle_update(cb(7, f"mq:ef:reward:{qid}"))
    assert "Сейчас: 100–500 монет" in client.answers[-1]["text"]
    await bot.handle_update(msg(7, "50"))
    quest = await get_quest(sessions, qid)
    assert (quest.reward_min, quest.reward) == (None, 50)
    student = await get_user(sessions, 5)
    await bot.handle_update(cb(7, f"jr:st:{student.id}"))
    assert "заработано: 200 монет" in client.answers[-1]["text"]


async def test_range_rules(env, fixed_ranges):
    bot, client, sessions = env
    await register(bot, ADMIN, "Админов Админ")
    # диапазон только для квеста с проверкой
    await bot.handle_update(cb(ADMIN, "menu:new_quest"))
    for text in ("Уборка", "Описание", "100-300"):
        await bot.handle_update(msg(ADMIN, text))
    await bot.handle_update(cb(ADMIN, "nq:kind:auto"))
    assert "только для квеста с проверкой" in client.answers[-1]["notification"]
    await bot.handle_update(cb(ADMIN, "cancel"))
    # учитель меняет награду своего квеста только в пределах 50–150
    await make_teacher(bot, 7)
    await create_quest(bot, 7)
    qid = await quest_id(sessions)
    await bot.handle_update(cb(7, f"mq:ef:reward:{qid}"))
    await bot.handle_update(msg(7, "100-600"))
    assert "50–150" in client.sent[-1][1]
    await bot.handle_update(msg(7, "150-50"))
    assert "от меньшего к большему" in client.sent[-1][1]
    await bot.handle_update(msg(7, "60-120"))
    quest = await get_quest(sessions, qid)
    assert (quest.reward_min, quest.reward) == (60, 120)


# --- проверяющие ежедневных и назначенных квестов ---


async def pe_setup(bot, sessions):
    """Учитель 7 ведёт Тестовый класс, физрук 8 — учитель 5Б; админ создаёт ежедневный видео-квест."""
    from tests.test_bot import add_class

    await make_teacher(bot, 7)
    await add_class(sessions, "5Б")
    await register(bot, 8, "Физрукова Елена")
    await bot.handle_update(msg(ADMIN, "/role 8 teacher"))
    await assign_class(bot, 8, "5Б")
    await create_quest(bot, ADMIN, title="Зарядка", kind="photo", repeat="daily")
    await register(bot, 5)
    return await quest_id(sessions, "Зарядка")


async def test_daily_quest_answers_go_to_admin_only(env):
    bot, client, sessions = env
    await pe_setup(bot, sessions)
    client.sent.clear()
    sub = await submit_text(bot, sessions, 5, "Сделал", title="Зарядка")
    notified = {to for to, text, _ in client.sent if "Новая заявка" in text}
    assert notified == {ADMIN}  # классный учитель 7 не получает
    await bot.handle_update(cb(7, "menu:review"))
    assert f"rev:open:{sub.id}" not in str(client.answers[-1]["attachments"])
    await bot.handle_update(cb(7, f"rev:ok:{sub.id}"))
    assert "не найдена" in client.answers[-1]["notification"]


async def test_assigned_reviewer(env):
    bot, client, sessions = env
    qid = await pe_setup(bot, sessions)
    pe = await get_user(sessions, 8)

    await bot.handle_update(cb(ADMIN, f"mq:ef:reviewers:{qid}"))
    assert "не назначены" in client.answers[-1]["text"]
    await bot.handle_update(cb(ADMIN, f"mq:rv:{pe.id}:{qid}"))
    assert "Проверяющие квеста «Зарядка»: Физрукова Елена" in client.answers[-1]["text"]
    await bot.handle_update(cb(ADMIN, f"mq:open:{qid}"))
    assert "Проверяют: Физрукова Елена" in client.answers[-1]["text"]

    client.sent.clear()
    sub = await submit_text(bot, sessions, 5, "Сделал", title="Зарядка")
    notified = {to for to, text, _ in client.sent if "Новая заявка" in text}
    assert notified == {ADMIN, 8}  # физрук получает, хотя ученик не из его класса
    await bot.handle_update(cb(7, f"rev:ok:{sub.id}"))
    assert "не найдена" in client.answers[-1]["notification"]
    await bot.handle_update(cb(8, f"rev:ok:{sub.id}"))
    assert (await get_user(sessions, 5)).balance == 50

    # учитель не может назначать проверяющих чужому квесту
    await bot.handle_update(cb(7, f"mq:rv:{pe.id}:{qid}"))
    assert "не найден" in client.answers[-1]["notification"]
