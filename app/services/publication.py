"""Публикация засчитанных ответов в канале школы.

Для квестов с channel_post проверяющий после засчитывания решает: опубликовать,
вернуть автору на доработку (например, исправить ошибки) или не публиковать.
Монеты начисляются при засчитывании и от публикации не зависят.
"""
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Role, Submission, SubmissionStatus, User
from app.services import settings as settings_service
from app.services.quests import ANSWER_MAX, REASON_MAX

OFFERED = "offered"
REWORK = "rework"
PUBLISHED = "published"
SKIPPED = "skipped"


class PublicationError(Exception):
    pass


async def offer(session: AsyncSession, submission: Submission) -> bool:
    """После засчитывания: предложить публикацию, если квест публикуемый и канал подключён."""
    if (
        not submission.quest.channel_post
        or submission.status != SubmissionStatus.approved
        or submission.publication is not None
        or await settings_service.channel(session) is None
    ):
        return False
    submission.publication = OFFERED
    await session.commit()
    return True


def can_decide(user: User, submission: Submission) -> bool:
    """Решает проверяющий, засчитавший ответ, или админ."""
    return user.role == Role.admin or submission.reviewer_id == user.id


async def _get(session: AsyncSession, submission_id: int) -> Submission:
    submission = await session.get(Submission, submission_id)
    if submission is None or submission.publication is None:
        raise PublicationError("Публикация не найдена.")
    return submission


async def for_decision(session: AsyncSession, user: User, submission_id: int) -> Submission:
    submission = await _get(session, submission_id)
    if not can_decide(user, submission):
        raise PublicationError("Решение о публикации принимает проверяющий или админ.")
    if submission.status != SubmissionStatus.approved:
        raise PublicationError("Ответ больше не засчитан — публиковать нечего.")
    if submission.publication == PUBLISHED:
        raise PublicationError("Уже опубликовано.")
    if submission.publication == SKIPPED:
        raise PublicationError("Решено не публиковать.")
    if submission.publication == REWORK:
        raise PublicationError("Ответ у автора на доработке — дождитесь исправленного варианта.")
    return submission


def content(submission: Submission) -> tuple[str | None, dict | None, dict | None]:
    """Что публикуем: исправленный автором вариант или исходный ответ."""
    draft = submission.publication_draft
    if draft:
        return draft.get("text"), draft.get("photo"), draft.get("video")
    return submission.text, submission.photo, submission.video


def author_line(student: User) -> str:
    """«Имя Фамилия, класс». В базе ФИО записано как «Фамилия Имя»."""
    parts = (student.full_name or student.display_name).split()
    name = " ".join(parts[1:2] + parts[:1]) if len(parts) >= 2 else " ".join(parts)
    klass = student.school_class.name if student.school_class else None
    return f"{name}, {klass}" if klass else name


def post_text(submission: Submission) -> str:
    text, _, _ = content(submission)
    body = f"\n\n{text}" if text else ""
    return f"📰 {submission.quest.title}{body}\n\n✍️ Автор: {author_line(submission.user)}"


async def mark(
    session: AsyncSession, submission: Submission, status: str, note: str | None = None
) -> None:
    if status == REWORK:
        note = (note or "").strip()
        if not note:
            raise PublicationError("Напишите, что нужно исправить.")
        if len(note) > REASON_MAX:
            raise PublicationError(f"Комментарий: до {REASON_MAX} символов.")
        submission.publication_note = note
    submission.publication = status
    await session.commit()


async def for_fix(session: AsyncSession, student: User, submission_id: int) -> Submission:
    submission = await _get(session, submission_id)
    if submission.user_id != student.id:
        raise PublicationError("Публикация не найдена.")
    if submission.publication != REWORK:
        raise PublicationError("Исправленный вариант уже отправлен.")
    return submission


async def save_fix(
    session: AsyncSession,
    student: User,
    submission_id: int,
    text: str,
    photo: dict | None,
    video: dict | None,
) -> Submission:
    """Автор присылает исправленный вариант — он снова ждёт решения проверяющего."""
    submission = await for_fix(session, student, submission_id)
    if not text and photo is None and video is None:
        raise PublicationError("Пришлите исправленный текст, фото или видео.")
    if len(text) > ANSWER_MAX:
        raise PublicationError(f"Слишком длинно: {len(text)} символов, максимум {ANSWER_MAX}.")
    submission.publication_draft = {"text": text or None, "photo": photo, "video": video}
    submission.publication = OFFERED
    await session.commit()
    return submission
