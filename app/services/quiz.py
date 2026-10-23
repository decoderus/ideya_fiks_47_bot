"""Вопрос дня: каждому ученику свой случайный вопрос из банка, одна попытка в день.

Вопрос выбирается детерминированно по ученику, квесту и дню (по Москве): весь день
он один и тот же, у соседей по классу — разные. Вопросы, на которые ученик уже
отвечал за последние REPEAT_AFTER_DAYS дней, не повторяются. Порядок вариантов
перемешивается, чтобы верный ответ не стоял всегда на одном месте.
"""
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Quest, QuestKind, Submission, SubmissionStatus, User
from app.quiz_bank import DISABLED, LETTERS, QUESTIONS  # noqa: F401  LETTERS — для экранов
from app.services.quests import QuestError, get_open_quest

MSK = timezone(timedelta(hours=3))
REPEAT_AFTER_DAYS = 90


@dataclass
class DailyQuestion:
    index: int  # номер в банке
    subject: str
    text: str
    options: list[str]  # в порядке показа
    correct: int  # позиция верного варианта в options


def _today(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(MSK).date().isoformat()


async def today_question(
    session: AsyncSession, student: User, quest: Quest, now: datetime | None = None
) -> DailyQuestion:
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=REPEAT_AFTER_DAYS)
    rows = await session.execute(
        select(Submission.question_id, Submission.created_at).where(
            Submission.user_id == student.id,
            Submission.quest_id == quest.id,
            Submission.question_id.is_not(None),
        )
    )
    day_start = now.astimezone(MSK).replace(hour=0, minute=0, second=0, microsecond=0)
    recent = set()
    for question_id, created_at in rows:
        moment = created_at if created_at.tzinfo else created_at.replace(tzinfo=timezone.utc)
        # Вопрос, на который уже ответили сегодня, остаётся «вопросом дня»
        if since <= moment < day_start:
            recent.add(question_id)
    pool = [i for i in range(len(QUESTIONS)) if i not in DISABLED and i not in recent]
    if not pool:
        pool = [i for i in range(len(QUESTIONS)) if i not in DISABLED]
    seed = f"{student.id}:{quest.id}:{_today(now)}"
    index = random.Random(seed).choice(pool)
    subject, text, answers = QUESTIONS[index]
    order = list(range(4))
    random.Random(f"{seed}:{index}").shuffle(order)
    return DailyQuestion(
        index=index,
        subject=subject,
        text=text,
        options=[answers[i] for i in order],
        correct=order.index(0),
    )


@dataclass
class QuizResult:
    correct: bool
    question: DailyQuestion
    chosen: int
    submission: Submission


async def answer(
    session: AsyncSession, student: User, quest_id: int, question_index: int, chosen: int
) -> QuizResult:
    quest = await get_open_quest(session, student, quest_id)  # уже отвечал сегодня — ошибка
    if quest.kind != QuestKind.quiz:
        raise QuestError("Это не квест с вопросом.")
    question = await today_question(session, student, quest)
    if question.index != question_index:
        raise QuestError("Этот вопрос уже не актуален — откройте квест заново.")
    if not 0 <= chosen < 4:
        raise QuestError("Выберите один из вариантов.")
    correct = chosen == question.correct
    reward = quest.reward if correct else 0
    submission = Submission(
        quest=quest,
        user=student,
        status=SubmissionStatus.approved if correct else SubmissionStatus.rejected,
        question_id=question.index,
        reward=reward if correct else None,
        text=(
            f"{question.text}\nОтвет: {question.options[chosen]}"
            + ("" if correct else f" (верно: {question.options[question.correct]})")
        ),
    )
    if correct:
        student.balance += reward
    session.add(submission)
    await session.commit()
    return QuizResult(correct, question, chosen, submission)
