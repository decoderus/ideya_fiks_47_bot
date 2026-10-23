"""Вопросы учеников: по квесту и общие.

Ученик задаёт вопрос под любым доступным ему квестом — по заданию или по проверке —
или общий, кнопкой «❓ Задать вопрос» в меню. Вопрос по квесту получают все админы,
модераторы и учитель — автор квеста, общий — админы и модераторы. Ответить может
любой из получивших; после ответа учителя или модератора админ может уточнить ответ
(один раз, пока ответа админа нет).
"""
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Quest, QuestAnswer, QuestQuestion, Role, User
from app.keyboards import ROLE_TITLES
from app.services.quests import _visible_to

QUESTION_MAX = 1000
ANSWER_MAX = 2000
LIST_LIMIT = 15


class QuestionError(Exception):
    """Ошибка, текст которой можно показать пользователю."""


def about(question: QuestQuestion) -> str:
    """« по квесту «Название»» — для текстов; у общего вопроса пусто."""
    return f" по квесту «{question.quest.title}»" if question.quest else ""


def signature(answer: QuestAnswer) -> str:
    """«— Петрова Анна, учитель»: кто ответил и в какой роли."""
    return f"— {answer.author.display_name}, {ROLE_TITLES[answer.author_role]}"


async def quest_for_question(session: AsyncSession, student: User, quest_id: int) -> Quest:
    """Квест, по которому ученик может задать вопрос: любой видимый ему, даже выполненный."""
    if student.role != Role.student:
        raise QuestionError("Вопросы по квестам задают ученики.")
    quest = await session.scalar(select(Quest).where(Quest.id == quest_id, _visible_to(student)))
    if quest is None:
        raise QuestionError("Квест не найден или уже закрыт.")
    return quest


async def ask(
    session: AsyncSession, student: User, quest_id: int | None, text: str
) -> QuestQuestion:
    """quest_id=None — общий вопрос администрации."""
    if quest_id is None:
        if student.role != Role.student:
            raise QuestionError("Вопросы задают ученики.")
    else:
        quest_id = (await quest_for_question(session, student, quest_id)).id
    text = (text or "").strip()
    if not text:
        raise QuestionError("Напишите вопрос текстом.")
    if len(text) > QUESTION_MAX:
        raise QuestionError(f"Слишком длинный вопрос, максимум {QUESTION_MAX} символов.")
    question = QuestQuestion(quest_id=quest_id, student_id=student.id, text=text)
    session.add(question)
    await session.commit()
    return await get(session, question.id)


async def get(session: AsyncSession, question_id: int) -> QuestQuestion:
    question = await session.get(QuestQuestion, question_id, populate_existing=True)
    if question is None:
        raise QuestionError("Вопрос не найден.")
    return question


async def recipients(session: AsyncSession, question: QuestQuestion) -> list[User]:
    """Админы, модераторы и учитель — автор квеста (у общего вопроса — без учителя)."""
    conditions = [User.role.in_((Role.admin, Role.moderator))]
    if question.quest and question.quest.author.role == Role.teacher:
        conditions.append(User.id == question.quest.author_id)
    rows = await session.scalars(select(User).where(or_(*conditions)).order_by(User.id))
    return list(rows.unique())


def can_see(user: User, question: QuestQuestion) -> bool:
    if user.role in (Role.admin, Role.moderator):
        return True
    return (
        user.role == Role.teacher
        and question.quest is not None
        and question.quest.author_id == user.id
    )


def answer_block(user: User, question: QuestQuestion) -> str | None:
    """Почему пользователь не может ответить сейчас; None — может."""
    if not can_see(user, question):
        if question.quest is None:
            return "На общие вопросы отвечают админы и модераторы."
        return "Отвечают админы, модераторы и учитель — автор квеста."
    if not question.answers:
        return None
    if user.role == Role.admin and not any(a.author_role == Role.admin for a in question.answers):
        return None  # уточнение ответа учителя или модератора
    first = question.answers[0]
    return f"На вопрос уже ответил(а) {first.author.display_name} ({ROLE_TITLES[first.author_role]})."


def is_clarification(user: User, question: QuestQuestion) -> bool:
    return user.role == Role.admin and bool(question.answers)


async def answer(session: AsyncSession, user: User, question_id: int, text: str) -> QuestAnswer:
    question = await get(session, question_id)
    block = answer_block(user, question)
    if block:
        raise QuestionError(block)
    text = (text or "").strip()
    if not text:
        raise QuestionError("Напишите ответ текстом.")
    if len(text) > ANSWER_MAX:
        raise QuestionError(f"Слишком длинный ответ, максимум {ANSWER_MAX} символов.")
    item = QuestAnswer(question_id=question.id, author_id=user.id, author_role=user.role, text=text)
    session.add(item)
    await session.commit()
    await get(session, question.id)  # обновить список ответов
    return item


async def for_staff(session: AsyncSession, user: User) -> list[QuestQuestion]:
    """Последние вопросы, которые пользователь видит: без ответа — первыми."""
    query = select(QuestQuestion).order_by(QuestQuestion.id.desc())
    if user.role == Role.teacher:
        query = query.join(Quest).where(Quest.author_id == user.id)
    elif user.role not in (Role.admin, Role.moderator):
        return []
    items = list((await session.scalars(query.limit(100))).unique())
    items.sort(key=lambda q: bool(q.answers))  # стабильно: внутри групп — новые сверху
    return items[:LIST_LIMIT]
