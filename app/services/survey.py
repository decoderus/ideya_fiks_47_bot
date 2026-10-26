"""Опрос учеников и учителей: 4 вопроса кнопками + пожелание текстом.

Пройти можно один раз. Ученик получает REWARD монет за участие (не за «правильные» ответы).
Админ видит только итоги: доли вариантов, NPS и пожелания — без имён.
Опрос открывает отправка приглашений; повторная отправка — только тем, кто не прошёл.
"""
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CoinTransaction, Role, SurveyResponse, User
from app.services import settings as settings_service

SURVEY = "2026-autumn"  # идентификатор текущего опроса
OPEN_KEY = "survey_open"
REWARD = 100
REWARD_REASON = "📋 Спасибо за участие в опросе"
COMMENT_MAX = 1000


@dataclass(frozen=True)
class Question:
    key: str
    text: str
    options: tuple[tuple[str, str], ...]  # (ключ, подпись)
    nps: bool = False


NPS_OPTIONS = tuple((str(i), str(i)) for i in range(11))

STUDENT = (
    Question("like", "Как тебе бот?", (
        ("love", "😍 Очень нравится"), ("like", "🙂 Нравится"),
        ("meh", "😐 Так себе"), ("dislike", "🙁 Не нравится"),
    )),
    Question("motive", "Что мотивирует больше всего?", (
        ("shop", "🛒 Монеты и магазин"), ("rating", "🏆 Рейтинг"),
        ("achievements", "🏅 Достижения и серии"), ("contest", "👥 Соревнование классов"),
        ("quests", "🎯 Сами задания"),
    )),
    Question("habits", "Стал(а) ли ты чаще делать полезные дела — зарядку, помощь дома, конкурсы?", (
        ("much", "💪 Да, заметно"), ("bit", "🙂 Немного"), ("no", "🤷 Нет"),
    )),
    Question("nps", "Насколько вероятно, что ты посоветуешь бот другу из другой школы? "
             "0 — точно нет, 10 — обязательно.", NPS_OPTIONS, nps=True),
)

TEACHER = (
    Question("helps", "Помогает ли бот поощрять активность учеников?", (
        ("much", "💯 Заметно"), ("bit", "🙂 Немного"), ("no", "🤷 Пока нет"),
    )),
    Question("time", "Сколько времени в неделю уходит на работу с ботом?", (
        ("10", "до 10 мин"), ("30", "10–30 мин"), ("60", "30–60 мин"), ("more", "больше часа"),
    )),
    Question("useful", "Что полезнее всего?", (
        ("quests", "🎯 Квесты и проверка"), ("journal", "📒 Журнал успехов"),
        ("coins", "💰 Монеты"), ("rating", "🏆 Рейтинг и соревнование"),
        ("questions", "❓ Вопросы и оповещения"),
    )),
    Question("nps", "Насколько вероятно, что вы посоветуете бот коллегам из другой школы? "
             "0 — точно нет, 10 — обязательно.", NPS_OPTIONS, nps=True),
)

QUESTIONS = {Role.student: STUDENT, Role.teacher: TEACHER}


class SurveyError(Exception):
    """Ошибка, текст которой можно показать пользователю."""


def questions_for(user: User) -> tuple[Question, ...] | None:
    return QUESTIONS.get(user.role)


async def is_open(session: AsyncSession) -> bool:
    return await settings_service.get_flag(session, OPEN_KEY)


async def set_open(session: AsyncSession, value: bool) -> None:
    await settings_service.set_flag(session, OPEN_KEY, value)


async def answered(session: AsyncSession, user: User) -> bool:
    return await session.scalar(
        select(SurveyResponse.id).where(SurveyResponse.survey == SURVEY, SurveyResponse.user_id == user.id)
    ) is not None


async def pending(session: AsyncSession, user: User) -> bool:
    """Показывать ли пользователю «📋 Опрос»: открыт, его роль опрашивается, ещё не прошёл."""
    if user.full_name is None or questions_for(user) is None:
        return False
    return await is_open(session) and not await answered(session, user)


async def recipients(session: AsyncSession) -> list[User]:
    """Ученики и учителя, которые ещё не прошли опрос."""
    done = select(SurveyResponse.user_id).where(SurveyResponse.survey == SURVEY)
    rows = await session.scalars(
        select(User).where(
            User.role.in_(tuple(QUESTIONS)),
            User.full_name.is_not(None),
            User.id.not_in(done),
        ).order_by(User.id)
    )
    return list(rows.unique())


def validate(user: User, answers: dict) -> None:
    questions = questions_for(user)
    if questions is None:
        raise SurveyError("Опрос — для учеников и учителей.")
    for q in questions:
        if answers.get(q.key) not in {key for key, _ in q.options}:
            raise SurveyError("Ответьте на все вопросы.")


async def submit(session: AsyncSession, user: User, answers: dict, comment: str | None) -> int:
    """Сохранить ответ. Возвращает начисленную награду (ученику — REWARD, учителю — 0)."""
    if not await is_open(session):
        raise SurveyError("Опрос уже закрыт. Спасибо!")
    validate(user, answers)
    comment = (comment or "").strip()[:COMMENT_MAX] or None
    reward = REWARD if user.role == Role.student else 0
    session.add(SurveyResponse(survey=SURVEY, user_id=user.id, role=user.role, answers=answers, comment=comment))
    if reward:
        user.balance += reward
        session.add(CoinTransaction(user_id=user.id, actor_id=user.id, amount=reward, reason=REWARD_REASON))
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise SurveyError("Вы уже прошли этот опрос. Спасибо!") from None
    return reward


@dataclass
class RoleResults:
    role: Role
    count: int
    distribution: dict[str, Counter]  # вопрос -> счётчик вариантов
    nps: int | None
    comments: list[str]


def nps(scores: list[int]) -> int | None:
    """Индекс готовности рекомендовать: % «9–10» минус % «0–6», от −100 до +100."""
    if not scores:
        return None
    promoters = sum(1 for s in scores if s >= 9)
    detractors = sum(1 for s in scores if s <= 6)
    return round(100 * (promoters - detractors) / len(scores))


async def results(session: AsyncSession, role: Role) -> RoleResults:
    rows = list(
        await session.scalars(
            select(SurveyResponse)
            .where(SurveyResponse.survey == SURVEY, SurveyResponse.role == role)
            .order_by(SurveyResponse.id.desc())
        )
    )
    distribution = {q.key: Counter() for q in QUESTIONS[role]}
    for r in rows:
        for key, value in r.answers.items():
            if key in distribution:
                distribution[key][value] += 1
    scores = [int(r.answers["nps"]) for r in rows if str(r.answers.get("nps", "")).isdigit()]
    return RoleResults(role, len(rows), distribution, nps(scores), [r.comment for r in rows if r.comment])
