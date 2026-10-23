"""Вопрос дня: свой вопрос каждому, одна попытка в день, серии."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.bot import Bot
from app.db.models import Quest, QuestKind, QuestRepeat, Submission, SubmissionStatus, User
from app.quiz_bank import QUESTIONS
from app.services import achievements as ach_service
from app.services import quiz as quiz_service
from tests.test_bot import ADMIN, buttons, cb, get_user, register
from tests.test_bot import env as plain_env  # noqa: F401


@pytest.fixture
async def env(plain_env, monkeypatch):  # noqa: F811
    """Бот с автодостижениями (серии), учёт достижений — с прошлого."""
    monkeypatch.setattr(ach_service, "START", datetime(2020, 1, 1, tzinfo=timezone.utc))
    bot, client, sessions = plain_env
    return Bot(client, sessions, admin_ids={ADMIN}, background=False), client, sessions


async def make_quiz(sessions) -> int:
    async with sessions() as s:
        admin = User(max_user_id=999, name="A", full_name="Админов Админ", role="admin")
        s.add(admin)
        await s.flush()
        quest = Quest(
            title="Ежедневная активность", description="Вопрос дня", reward=10,
            kind=QuestKind.quiz, repeat=QuestRepeat.daily, author_id=admin.id,
        )
        s.add(quest)
        await s.commit()
        return quest.id


def options(answer) -> list[str]:
    return [b["payload"] for b in buttons(answer["attachments"]) if b["payload"].startswith("q:quiz:")]


async def question_for(sessions, uid, qid):
    async with sessions() as s:
        user = await s.scalar(select(User).where(User.max_user_id == uid))
        quest = await s.get(Quest, qid)
        return await quiz_service.today_question(s, user, quest)


async def test_correct_answer(env):
    bot, client, sessions = env
    qid = await make_quiz(sessions)
    await register(bot, 5)
    q = await question_for(sessions, 5, qid)
    assert sorted(q.options) == sorted(QUESTIONS[q.index][2])
    assert q.options[q.correct] == QUESTIONS[q.index][2][0]

    await bot.handle_update(cb(5, f"q:open:{qid}"))
    answer = client.answers[-1]
    assert q.text in answer["text"] and "попытка одна" in answer["text"]
    assert options(answer) == [f"q:quiz:{qid}:{q.index}:{i}" for i in range(4)]
    # тот же вопрос при повторном открытии
    await bot.handle_update(cb(5, f"q:open:{qid}"))
    assert options(client.answers[-1]) == [f"q:quiz:{qid}:{q.index}:{i}" for i in range(4)]

    await bot.handle_update(cb(5, f"q:quiz:{qid}:{q.index}:{q.correct}"))
    assert "✅ Верно" in client.answers[-1]["text"]
    assert (await get_user(sessions, 5)).balance == 10 + 20  # + «Первый шаг»
    # вторая попытка в тот же день — нельзя
    await bot.handle_update(cb(5, f"q:quiz:{qid}:{q.index}:{q.correct}"))
    assert "снова можно завтра" in client.answers[-1]["notification"]
    assert (await get_user(sessions, 5)).balance == 30


async def test_wrong_answer_blocks_until_tomorrow(env):
    bot, client, sessions = env
    qid = await make_quiz(sessions)
    await register(bot, 5)
    q = await question_for(sessions, 5, qid)
    wrong = (q.correct + 1) % 4
    await bot.handle_update(cb(5, f"q:quiz:{qid}:{q.index}:{wrong}"))
    text = client.answers[-1]["text"]
    assert "❌ Ваш ответ" in text and q.options[q.correct] in text
    assert (await get_user(sessions, 5)).balance == 0
    await bot.handle_update(cb(5, f"q:quiz:{qid}:{q.index}:{q.correct}"))
    assert "снова можно завтра" in client.answers[-1]["notification"]
    # квест пропал из списка на сегодня
    await bot.handle_update(cb(5, "menu:quests"))
    attachments = client.answers[-1]["attachments"] or []
    assert f"q:open:{qid}" not in [b["payload"] for b in buttons(attachments)] if attachments else True


async def test_stale_question_and_new_day(env):
    bot, client, sessions = env
    qid = await make_quiz(sessions)
    await register(bot, 5)
    q = await question_for(sessions, 5, qid)
    other = (q.index + 1) % len(QUESTIONS)
    await bot.handle_update(cb(5, f"q:quiz:{qid}:{other}:0"))
    assert "не актуален" in client.answers[-1]["notification"]

    await bot.handle_update(cb(5, f"q:quiz:{qid}:{q.index}:{q.correct}"))
    # «наступил завтрашний день»: сдвигаем ответ в прошлое
    async with sessions() as s:
        sub = await s.scalar(select(Submission))
        sub.created_at = sub.created_at - timedelta(days=1)
        await s.commit()
    q2 = await question_for(sessions, 5, qid)
    assert q2.index != q.index  # вчерашний вопрос не повторяется
    await bot.handle_update(cb(5, f"q:quiz:{qid}:{q2.index}:{q2.correct}"))
    assert (await get_user(sessions, 5)).balance == 2 * 10 + 20  # + «Первый шаг»


async def test_students_get_different_questions(env):
    bot, client, sessions = env
    qid = await make_quiz(sessions)
    for uid in range(1, 9):
        await register(bot, uid, f"Ученик Номер{chr(ord('а') + uid)}")
    indices = {(await question_for(sessions, uid, qid)).index for uid in range(1, 9)}
    assert len(indices) > 1


async def test_week_streak_achievement(env):
    bot, client, sessions = env
    qid = await make_quiz(sessions)
    await register(bot, 5)
    student = await get_user(sessions, 5)
    async with sessions() as s:
        for days_ago in range(1, 7):  # 6 прошлых дней с верным ответом
            sub = Submission(
                quest_id=qid, user_id=student.id, status=SubmissionStatus.approved,
                reward=10, question_id=days_ago,
            )
            s.add(sub)
            await s.flush()
            sub.created_at = sub.created_at - timedelta(days=days_ago)
        await s.commit()
    q = await question_for(sessions, 5, qid)
    await bot.handle_update(cb(5, f"q:quiz:{qid}:{q.index}:{q.correct}"))
    notices = [t for to, t, _ in client.sent if to == 5 and "Новое достижение" in t]
    assert any("Ежедневная активность: неделя подряд" in t for t in notices)


async def test_quiz_not_in_review_archive(env):
    bot, client, sessions = env
    qid = await make_quiz(sessions)
    await register(bot, ADMIN, "Админов Админ")
    await register(bot, 5)
    q = await question_for(sessions, 5, qid)
    await bot.handle_update(cb(5, f"q:quiz:{qid}:{q.index}:{(q.correct + 1) % 4}"))
    await bot.handle_update(cb(ADMIN, "arch:page:0"))
    assert "Проверенных заявок пока нет" in client.answers[-1]["text"]
