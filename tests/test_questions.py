"""Вопросы учеников по квестам и ответы на них."""
from tests.test_bot import (  # noqa: F401  env — фикстура
    ADMIN,
    PHOTO,
    buttons,
    cb,
    create_quest,
    env,
    msg,
    quest_id,
    register,
    submit_text,
)
from tests.test_moderator import MOD, school

OTHER_TEACHER = 8


def notices(client, uid: int) -> list[tuple[str, list]]:
    return [(text, att) for to, text, att in client.sent if to == uid]


def payloads(attachments) -> list[str]:
    return [b["payload"] for b in buttons(attachments)]


async def setup(bot, sessions, author=7, kind="auto"):
    """Школа из test_moderator, ещё один учитель и квест автора author."""
    await school(bot, sessions)
    await register(bot, OTHER_TEACHER, "Другая Учительница")
    await bot.handle_update(msg(ADMIN, f"/role {OTHER_TEACHER} teacher"))
    await create_quest(bot, author, title="Уборка", kind=kind)
    return await quest_id(sessions, "Уборка")


async def ask(bot, client, qid, text="Можно убрать не весь класс, а только свою парту?"):
    await bot.handle_update(cb(5, f"q:open:{qid}"))
    assert f"qa:ask:{qid}" in payloads(client.answers[-1]["attachments"])
    await bot.handle_update(cb(5, f"qa:ask:{qid}"))
    assert "Напишите вопрос" in client.answers[-1]["text"]
    client.sent.clear()
    await bot.handle_update(msg(5, text))


async def question_id(client) -> int:
    for _, _, att in reversed(client.sent):
        for p in payloads(att) if att else []:
            if p.startswith("qa:ans:"):
                return int(p.rsplit(":", 1)[1])
    raise AssertionError("нет кнопки ответа")


async def test_question_goes_to_staff_and_quest_author(env):
    bot, client, sessions = env
    qid = await setup(bot, sessions)
    await ask(bot, client, qid)
    assert "Вопрос отправлен" in notices(client, 5)[-1][0]
    to = {uid for uid, text, _ in client.sent if "❓ Вопрос по квесту «Уборка»" in text}
    assert to == {ADMIN, MOD, 7}  # другой учитель не получает
    text, att = notices(client, 7)[-1]
    assert "От: Иванов Иван, Тестовый класс" in text and "свою парту" in text
    assert any(p.startswith("qa:ans:") for p in payloads(att))


async def test_teacher_answers_admin_clarifies(env):
    bot, client, sessions = env
    qid = await setup(bot, sessions)
    await ask(bot, client, qid)
    question = await question_id(client)

    client.sent.clear()
    await bot.handle_update(cb(7, f"qa:ans:{question}"))
    assert "Ученик увидит ваше имя и роль (учитель)" in client.answers[-1]["text"]
    await bot.handle_update(msg(7, "Да, достаточно своей парты."))
    text, att = notices(client, 5)[-1]
    assert "💬 Ответ на ваш вопрос по квесту «Уборка»" in text
    assert "Да, достаточно своей парты.\n— Учителева Анна, учитель" in text
    assert f"q:open:{qid}" in payloads(att)
    admin_text, admin_att = notices(client, ADMIN)[-1]
    assert "Учителева Анна (учитель) ответил(а)" in admin_text
    assert payloads(admin_att) == [f"qa:ans:{question}"]
    assert buttons(admin_att)[0]["text"] == "✍️ Уточнить ответ"

    # модератор и другой учитель отвечать уже не могут
    await bot.handle_update(cb(MOD, f"qa:ans:{question}"))
    assert "уже ответил(а) Учителева Анна (учитель)" in client.answers[-1]["notification"]
    await bot.handle_update(cb(OTHER_TEACHER, f"qa:ans:{question}"))
    assert "автор квеста" in client.answers[-1]["notification"]

    # админ уточняет — один раз
    await bot.handle_update(cb(ADMIN, f"qa:ans:{question}"))
    assert "уточнение" in client.answers[-1]["text"]
    await bot.handle_update(msg(ADMIN, "И не забудьте вытереть доску."))
    text, _ = notices(client, 5)[-1]
    assert text.startswith("💬 Уточнение ответа на ваш вопрос")
    assert "— Админов Админ, админ" in text
    await bot.handle_update(cb(ADMIN, f"qa:ans:{question}"))
    assert "уже ответил(а)" in client.answers[-1]["notification"]

    # в карточке вопроса — оба ответа с подписями, кнопки ответа нет
    await bot.handle_update(cb(ADMIN, f"qa:open:{question}"))
    card = client.answers[-1]
    assert "— Учителева Анна, учитель" in card["text"] and "— Админов Админ, админ" in card["text"]
    assert not any(p.startswith("qa:ans:") for p in payloads(card["attachments"]))


async def test_admin_answer_is_final(env):
    bot, client, sessions = env
    qid = await setup(bot, sessions)
    await ask(bot, client, qid)
    question = await question_id(client)
    client.sent.clear()
    await bot.handle_update(cb(ADMIN, f"qa:ans:{question}"))
    await bot.handle_update(msg(ADMIN, "Да."))
    assert "— Админов Админ, админ" in notices(client, 5)[-1][0]
    # себе уведомление «ответил(а), уточните» не нужно — только подтверждение отправки
    assert [t for t, _ in notices(client, ADMIN) if "ответил(а)" in t] == []
    await bot.handle_update(cb(7, f"qa:ans:{question}"))
    assert "уже ответил(а) Админов Админ (админ)" in client.answers[-1]["notification"]


async def test_moderator_answer_signed_as_moderator(env):
    bot, client, sessions = env
    qid = await setup(bot, sessions, author=ADMIN)
    await ask(bot, client, qid)
    to = {uid for uid, text, _ in client.sent if "❓ Вопрос по квесту" in text}
    assert to == {ADMIN, MOD}  # квест админа: учителю не приходит
    question = await question_id(client)
    await bot.handle_update(cb(7, f"qa:open:{question}"))
    assert client.answers[-1]["notification"] == "Вопрос не найден."
    await bot.handle_update(cb(MOD, f"qa:ans:{question}"))
    await bot.handle_update(msg(MOD, "Подойдите к администратору после уроков."))
    assert "— Модеров Максим, модератор" in notices(client, 5)[-1][0]


async def test_ask_about_pending_answer(env):
    bot, client, sessions = env
    qid = await setup(bot, sessions, kind="photo")
    await submit_text(bot, sessions, 5, "Убрал", title="Уборка")
    await bot.handle_update(cb(5, f"q:open:{qid}"))
    card = client.answers[-1]
    assert "уже ждёт проверки" in card["text"]
    assert payloads(card["attachments"]) == [f"qa:ask:{qid}", "menu:quests", "menu:main"]
    await bot.handle_update(cb(5, f"qa:ask:{qid}"))
    await bot.handle_update(msg(5, "Когда проверят мой ответ?"))
    assert any("Когда проверят" in text for to, text, _ in client.sent if to == 7)


async def test_question_rules_and_list(env):
    bot, client, sessions = env
    qid = await setup(bot, sessions)
    # фото без текста и слишком длинный текст — не вопрос
    await bot.handle_update(cb(5, f"qa:ask:{qid}"))
    await bot.handle_update(msg(5, "", attachments=PHOTO))
    assert "Напишите текстом" in client.sent[-1][1]
    await bot.handle_update(msg(5, "х" * 1001))
    assert "максимум 1000" in client.sent[-1][1]
    await bot.handle_update(msg(5, "Первый вопрос"))
    # учитель и админ спрашивать не могут
    await bot.handle_update(cb(ADMIN, f"qa:ask:{qid}"))
    assert "задают ученики" in client.answers[-1]["notification"]

    # списки: ученику недоступен, другому учителю — пусто, автору — его вопрос
    await bot.handle_update(cb(5, "menu:questions"))
    assert "доступен учителям" in client.answers[-1]["notification"]
    await bot.handle_update(cb(OTHER_TEACHER, "menu:questions"))
    assert "Вопросов учеников пока нет" in client.answers[-1]["text"]
    await bot.handle_update(cb(7, "menu:questions"))
    answer = client.answers[-1]
    assert "Ждут ответа: 1" in answer["text"]
    assert buttons(answer["attachments"])[0]["text"].startswith("🆕 Уборка · Иванов Иван")


async def test_menus_have_questions(env):
    bot, client, sessions = env
    await school(bot, sessions)
    for uid in (ADMIN, MOD, 7):
        await bot.handle_update(msg(uid, "/start"))
        assert "menu:questions" in payloads(client.sent[-1][2]), uid
    await bot.handle_update(msg(5, "/start"))
    assert "menu:questions" not in payloads(client.sent[-1][2])


async def test_general_question_from_menu(env):
    bot, client, sessions = env
    await setup(bot, sessions)
    await bot.handle_update(msg(5, "/start"))
    assert "qa:ask:0" in payloads(client.sent[-1][2])
    await bot.handle_update(cb(5, "qa:ask:0"))
    assert "получат модераторы и администраторы" in client.answers[-1]["text"]
    client.sent.clear()
    await bot.handle_update(msg(5, "Как потратить монеты?"))
    text, att = notices(client, 5)[-1]
    assert "Вопрос отправлен" in text and "menu:main" in payloads(att)
    to = {uid for uid, text, _ in client.sent if text.startswith("❓ Общий вопрос")}
    assert to == {ADMIN, MOD}  # учителям общий вопрос не приходит
    question = await question_id(client)

    # учитель не видит и не отвечает
    await bot.handle_update(cb(7, "menu:questions"))
    assert "пока нет" in client.answers[-1]["text"]
    await bot.handle_update(cb(7, f"qa:ans:{question}"))
    assert "общие вопросы отвечают админы и модераторы" in client.answers[-1]["notification"]

    # модератор отвечает, админ может уточнить
    client.sent.clear()
    await bot.handle_update(cb(MOD, f"qa:ans:{question}"))
    await bot.handle_update(msg(MOD, "В разделе «🛒 Магазин»."))
    text, att = notices(client, 5)[-1]
    assert text.startswith("💬 Ответ на ваш вопрос\n\n❓ Как потратить монеты?")
    assert "— Модеров Максим, модератор" in text and "menu:main" in payloads(att)
    admin_text, admin_att = notices(client, ADMIN)[-1]
    assert "на вопрос ученика Иванов Иван." in admin_text  # без «по квесту»
    assert payloads(admin_att) == [f"qa:ans:{question}"]
    await bot.handle_update(cb(ADMIN, "menu:questions"))
    assert buttons(client.answers[-1]["attachments"])[0]["text"].startswith("✅ Общий вопрос")


async def test_only_students_ask_general(env):
    bot, client, sessions = env
    await school(bot, sessions)
    for uid in (ADMIN, MOD, 7):
        await bot.handle_update(cb(uid, "qa:ask:0"))
        assert "задают ученики" in client.answers[-1]["notification"], uid
