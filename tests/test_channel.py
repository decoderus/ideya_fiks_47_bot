"""Публикация засчитанных ответов в канале школы."""
from app.db.models import Quest, Submission
from app.services import settings as settings_service
from tests.test_bot import (  # noqa: F401  env — фикстура
    ADMIN,
    PHOTO,
    buttons,
    cb,
    create_quest,
    env,
    make_teacher,
    msg,
    quest_id,
    register,
    submit_text,
)

CHANNEL = -555


def payloads(attachments) -> list[str]:
    keyboards = [a for a in attachments or [] if a["type"] == "inline_keyboard"]
    return [b["payload"] for b in buttons(keyboards)] if keyboards else []


def last_to(client, uid):
    return next(m for m in reversed(client.sent) if m[0] == uid)


async def school(bot, sessions, channel=True):
    """Учитель 7, ученик 5 «Петров Пётр», публикуемый квест «Заметка»."""
    await make_teacher(bot, 7)
    await register(bot, 5, "Петров Пётр")
    await create_quest(bot, 7, title="Заметка", kind="photo")
    qid = await quest_id(sessions, "Заметка")
    async with sessions() as s:
        (await s.get(Quest, qid)).channel_post = True
        await s.commit()
    if channel:
        await bot.handle_update(
            {"update_type": "bot_added", "chat_id": CHANNEL, "is_channel": True, "user": {"user_id": ADMIN}}
        )


async def test_channel_connects_when_bot_added(env):
    bot, client, sessions = env
    await school(bot, sessions)
    async with sessions() as s:
        assert await settings_service.channel(s) == (CHANNEL, "Школьные новости")
    assert "подключён к каналу «Школьные новости»" in last_to(client, ADMIN)[1]
    # в обычный групповой чат — не подключаем
    await bot.handle_update({"update_type": "bot_added", "chat_id": -1, "is_channel": False})
    async with sessions() as s:
        assert (await settings_service.channel(s))[0] == CHANNEL


async def test_admin_connects_channel_by_forward(env):
    bot, client, sessions = env
    await school(bot, sessions, channel=False)
    forward = msg(ADMIN, "")
    forward["message"]["link"] = {"type": "forward", "chat_id": -777, "message": {"text": "пост"}}
    await bot.handle_update(forward)
    assert "Канал «Школьные новости» подключён" in last_to(client, ADMIN)[1]
    # пересылка от ученика ничего не меняет
    forward = msg(5, "")
    forward["message"]["link"] = {"type": "forward", "chat_id": -888}
    await bot.handle_update(forward)
    async with sessions() as s:
        assert (await settings_service.channel(s))[0] == -777


async def test_no_offer_without_channel_or_flag(env):
    bot, client, sessions = env
    await school(bot, sessions, channel=False)
    sub = await submit_text(bot, sessions, 5, "Заметка", title="Заметка")
    await bot.handle_update(cb(7, f"rev:ok:{sub.id}"))
    assert not any(p.startswith("pub:") for p in payloads(client.answers[-1]["attachments"]))


async def test_publish_after_rework(env):
    bot, client, sessions = env
    await school(bot, sessions)
    sub = await submit_text(bot, sessions, 5, "В субботу прошол субботник", title="Заметка")
    await bot.handle_update(cb(7, f"rev:ok:{sub.id}"))
    answer = client.answers[-1]
    assert "Опубликовать этот ответ в канале «Школьные новости»?" in answer["text"]
    assert {f"pub:ok:{sub.id}", f"pub:fix:{sub.id}", f"pub:no:{sub.id}"} <= set(payloads(answer["attachments"]))

    # чужой учитель не решает
    await register(bot, 8, "Другой Учитель")
    await bot.handle_update(msg(ADMIN, "/role 8 teacher"))
    await bot.handle_update(cb(8, f"pub:ok:{sub.id}"))
    assert "проверяющий или админ" in client.answers[-1]["notification"]

    # на доработку: комментарий обязателен, уходит автору
    await bot.handle_update(cb(7, f"pub:fix:{sub.id}"))
    await bot.handle_update(msg(7, "Исправьте «прошол»"))
    to, text, att = last_to(client, 5)
    assert "Комментарий проверяющего: Исправьте «прошол»" in text and "Монеты уже начислены" in text
    assert f"pub:send:{sub.id}" in payloads(att)
    await bot.handle_update(cb(7, f"pub:ok:{sub.id}"))
    assert "на доработке" in client.answers[-1]["notification"]

    # автор присылает исправленный вариант с фото
    await bot.handle_update(cb(5, f"pub:send:{sub.id}"))
    assert "Ваш текст сейчас:\nВ субботу прошол субботник" in client.answers[-1]["text"]
    await bot.handle_update(msg(5, "В субботу прошёл субботник", attachments=PHOTO))
    assert "отправлен проверяющему" in last_to(client, 5)[1]
    to, text, att = last_to(client, 7)
    assert "доработал(а) ответ" in text and "✍️ Автор: Пётр Петров, Тестовый класс" in text
    assert f"pub:ok:{sub.id}" in payloads(att)
    await bot.handle_update(cb(5, f"pub:send:{sub.id}"))
    assert "уже отправлен" in client.answers[-1]["notification"]

    # публикация: исправленный текст, фото, подпись «Имя Фамилия, класс»
    await bot.handle_update(cb(7, f"pub:ok:{sub.id}"))
    assert "Опубликовано в канале «Школьные новости»" in client.answers[-1]["text"]
    assert len(client.chat_posts) == 1
    chat_id, text, att = client.chat_posts[0]
    assert chat_id == CHANNEL
    assert text == "📰 Заметка\n\nВ субботу прошёл субботник\n\n✍️ Автор: Пётр Петров, Тестовый класс"
    assert att == [{"type": "image", "payload": {"token": "tok"}}]
    assert "опубликован в канале «Школьные новости»" in last_to(client, 5)[1]
    await bot.handle_update(cb(7, f"pub:ok:{sub.id}"))
    assert "Уже опубликовано" in client.answers[-1]["notification"]
    assert len(client.chat_posts) == 1


async def test_skip_publication(env):
    bot, client, sessions = env
    await school(bot, sessions)
    sub = await submit_text(bot, sessions, 5, "Коротко", title="Заметка")
    await bot.handle_update(cb(7, f"rev:ok:{sub.id}"))
    await bot.handle_update(cb(7, f"pub:no:{sub.id}"))
    assert "Решено не публиковать" in client.answers[-1]["text"]
    await bot.handle_update(cb(ADMIN, f"pub:ok:{sub.id}"))
    assert "не публиковать" in client.answers[-1]["notification"]
    assert client.chat_posts == []
    async with sessions() as s:
        assert (await s.get(Submission, sub.id)).publication == "skipped"


async def test_channel_connects_on_admin_rights(env):
    """Повторная выдача прав админа приходит как bot_admin_permissions_changed без is_channel."""
    bot, client, sessions = env
    await school(bot, sessions, channel=False)

    async def get_chat(chat_id):
        return {"chat_id": chat_id, "type": "channel" if chat_id == -42 else "chat", "title": "Новости"}

    client.get_chat = get_chat
    await bot.handle_update({"update_type": "bot_admin_permissions_changed", "chat_id": -7})
    async with sessions() as s:
        assert await settings_service.channel(s) is None  # группа — не канал
    await bot.handle_update({"update_type": "bot_admin_permissions_changed", "chat_id": -42})
    async with sessions() as s:
        assert await settings_service.channel(s) == (-42, "Новости")
    sent = len(client.sent)
    await bot.handle_update({"update_type": "bot_admin_permissions_changed", "chat_id": -42})
    assert len(client.sent) == sent  # повторно не уведомляем
