"""Квесты: создание, выполнение учениками, проверка учителями."""
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Quest,
    QuestKind,
    QuestRepeat,
    Role,
    SchoolClass,
    Submission,
    SubmissionStatus,
    User,
)
from app.services import achievements as ach_service
from app.services import settings as settings_service

# Квесты учителя и модератора — только с проверкой и с плавающей наградой:
# точную сумму из диапазона выбирает проверяющий
FIXED_RANGES = {Role.teacher: (50, 150), Role.moderator: (50, 300)}
# У админа лимита по смыслу нет, это лишь защита от опечатки в лишние нули
ADMIN_REWARD_LIMIT = 1_000_000

TITLE_MAX = 128
DESCRIPTION_MAX = 2000
# Текст ответа ученика: с запасом до лимита сообщения MAX (4000), куда добавятся данные заявки
ANSWER_MAX = 3000
REASON_MAX = 500  # причина отклонения
# Подтверждение квеста модератора админом (Quest.approval)
APPROVAL_PENDING = "pending"
APPROVAL_REWORK = "rework"
# Готовые причины отклонения (кнопки у проверяющего)
REJECT_REASONS = [
    "Не видно, что задание выполнено",
    "Ответ не соответствует заданию",
    "Дата на документе не подходит",
    "Нужно видео, а не фото",
    "Фото не ваше: из интернета или создано ИИ",
    "Нет листка с именем и датой",
]


class QuestError(Exception):
    """Ошибка, текст которой можно показать пользователю."""


MSK = timezone(timedelta(hours=3))

# Когда повторяемый квест снова станет доступен
NEXT_TIME = {
    QuestRepeat.daily: "завтра",
    QuestRepeat.weekly: "с понедельника",
    QuestRepeat.monthly: "с 1-го числа следующего месяца",
}


def period_start(repeat: QuestRepeat, now: datetime | None = None) -> datetime | None:
    """Начало текущего периода повторения по Москве, в UTC. None — квест не повторяется."""
    if repeat == QuestRepeat.none:
        return None
    local = (now or datetime.now(timezone.utc)).astimezone(MSK)
    local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    if repeat == QuestRepeat.weekly:
        local -= timedelta(days=local.weekday())
    elif repeat == QuestRepeat.monthly:
        local = local.replace(day=1)
    return local.astimezone(timezone.utc)


def _utc(moment: datetime) -> datetime:
    # SQLite отдаёт время без часового пояса; храним в UTC
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def can_create(user: User) -> bool:
    return user.role in (Role.teacher, Role.admin, Role.moderator)


def fixed_range(user: User) -> tuple[int, int] | None:
    """Награда новых квестов учителя и модератора: (минимум, максимум). У админа — None."""
    return FIXED_RANGES.get(user.role)


def reward_bounds(user: User) -> tuple[int, int]:
    """Допустимые суммы награды при создании и изменении квеста."""
    return fixed_range(user) or (1, ADMIN_REWARD_LIMIT)


def is_staff(user: User) -> bool:
    """Админ или модератор: работают со всей школой, а не со своими классами."""
    return user.role in (Role.admin, Role.moderator)


def parse_reward(user: User, text: str) -> int:
    try:
        reward = int(text.strip())
    except ValueError:
        raise QuestError("Награда должна быть целым числом.")
    low, high = reward_bounds(user)
    if not low <= reward <= high:
        raise QuestError(f"Награда должна быть от {low} до {high}.")
    return reward


def parse_reward_range(user: User, text: str) -> tuple[int | None, int]:
    """«300» -> (None, 300); «100-500» -> (100, 500): сумму выберет проверяющий."""
    match = re.fullmatch(r"\s*(\d+)\s*[-–—]\s*(\d+)\s*", text)
    if match is None:
        return None, parse_reward(user, text)
    low, high = int(match.group(1)), int(match.group(2))
    min_, max_ = reward_bounds(user)
    if not min_ <= low < high <= max_:
        raise QuestError(
            f"Диапазон награды: от меньшего к большему, в пределах {min_}–{max_}, "
            f"например {min_}-{min(max_, 500)}."
        )
    return low, high


def reward_options(quest: Quest) -> list[int]:
    """Суммы для кнопок проверяющего: 5 ступеней от минимума до максимума, кратные 10."""
    if quest.reward_min is None:
        return [quest.reward]
    low, high = quest.reward_min, quest.reward
    steps = [low + (high - low) * i / 4 for i in range(5)]
    values = [low] + [int(round(v / 10) * 10) for v in steps[1:-1]] + [high]
    return sorted({v for v in values if low <= v <= high})


def _check_range(kind: QuestKind, reward_min: int | None) -> None:
    if reward_min is not None and kind != QuestKind.photo:
        raise QuestError(
            "Диапазон награды возможен только у квеста с проверкой: сумму выбирает проверяющий."
        )


async def create_quest(
    session: AsyncSession,
    author: User,
    title: str,
    description: str,
    reward: int,
    kind: QuestKind,
    class_id: int | None,
    photo: dict | None = None,
    repeat: QuestRepeat = QuestRepeat.none,
    reward_min: int | None = None,
) -> Quest:
    """class_id: для какого класса квест, None — для всей школы.

    Учитель создаёт квесты только для своего класса, админ — для любого или для всех.
    """
    if not can_create(author):
        raise QuestError("Создавать квесты могут только учителя и админы.")
    fixed = fixed_range(author)
    if fixed is not None and (kind != QuestKind.photo or (reward_min, reward) != fixed):
        raise QuestError(
            f"Квест учителя или модератора — только с проверкой, награда {fixed[0]}–{fixed[1]} монет."
        )
    low, high = reward_bounds(author)
    if not low <= reward <= high:
        raise QuestError(f"Награда должна быть от {low} до {high}.")
    _check_range(kind, reward_min)
    if not is_staff(author) and class_id not in author.teacher_class_ids:
        raise QuestError("Учитель может создавать квесты только для своих классов.")
    if class_id is not None and await session.get(SchoolClass, class_id) is None:
        raise QuestError("Класс не найден.")
    quest = Quest(
        title=title[:TITLE_MAX],
        description=description[:DESCRIPTION_MAX],
        reward=reward,
        kind=kind,
        class_id=class_id,
        author=author,
        photo=photo,
        repeat=repeat,
        reward_min=reward_min,
    )
    if needs_approval(author):
        # Квест модератора публикует админ: до этого ученики его не видят
        quest.is_active = False
        quest.approval = APPROVAL_PENDING
    session.add(quest)
    await session.commit()
    return quest


def needs_approval(author: User) -> bool:
    return author.role == Role.moderator


async def _pending_approval(session: AsyncSession, admin: User, quest_id: int) -> Quest:
    if admin.role != Role.admin:
        raise QuestError("Публикацию квеста подтверждает админ.")
    quest = await session.get(Quest, quest_id)
    if quest is None:
        raise QuestError("Квест не найден.")
    if quest.approval != APPROVAL_PENDING:
        raise QuestError("Квест уже рассмотрен.")
    return quest


async def approve_quest(session: AsyncSession, admin: User, quest_id: int) -> Quest:
    """Админ публикует квест модератора."""
    quest = await _pending_approval(session, admin, quest_id)
    quest.approval, quest.approval_note, quest.is_active = None, None, True
    await session.commit()
    return quest


async def return_quest(
    session: AsyncSession, admin: User, quest_id: int, note: str | None = None
) -> Quest:
    """Админ возвращает квест модератору на доработку."""
    quest = await _pending_approval(session, admin, quest_id)
    note = (note or "").strip() or None
    if note and len(note) > REASON_MAX:
        raise QuestError(f"Комментарий: до {REASON_MAX} символов.")
    quest.approval, quest.approval_note = APPROVAL_REWORK, note
    await session.commit()
    return quest


async def resubmit_quest(session: AsyncSession, author: User, quest_id: int) -> Quest:
    """Модератор после доработки снова отправляет квест админу."""
    quest = await get_managed(session, author, quest_id)
    if quest.approval != APPROVAL_REWORK:
        raise QuestError("Квест не на доработке.")
    quest.approval = APPROVAL_PENDING
    await session.commit()
    return quest


def _visible_to(student: User):
    return Quest.is_active & or_(
        Quest.class_id.is_(None), Quest.class_id == student.class_id
    )


async def _statuses(
    session: AsyncSession, student: User, quests: list[Quest]
) -> dict[int, SubmissionStatus]:
    """Итоговый статус по каждому квесту: approved важнее pending, pending важнее rejected.

    У повторяемого квеста учитываются только ответы текущего периода (по времени отправки);
    ответ на проверке учитывается всегда — второй, пока не проверен первый, отправить нельзя.
    У вопроса дня неверный ответ тоже закрывает квест до следующего периода.
    """
    if not quests:
        return {}
    starts = {q.id: period_start(q.repeat) for q in quests}
    quiz_ids = {q.id for q in quests if q.kind == QuestKind.quiz}
    rows = await session.execute(
        select(Submission.quest_id, Submission.status, Submission.created_at).where(
            Submission.user_id == student.id, Submission.quest_id.in_(list(starts))
        )
    )
    priority = {
        SubmissionStatus.rejected: 0,
        SubmissionStatus.pending: 1,
        SubmissionStatus.approved: 2,
    }
    result: dict[int, SubmissionStatus] = {}
    for quest_id, status, created_at in rows:
        start = starts[quest_id]
        if start and status != SubmissionStatus.pending and _utc(created_at) < start:
            continue
        if quest_id in quiz_ids and status == SubmissionStatus.rejected:
            status = SubmissionStatus.approved  # попытка на сегодня использована
        if quest_id not in result or priority[status] > priority[result[quest_id]]:
            result[quest_id] = status
    return result


async def available_quests(
    session: AsyncSession, student: User
) -> list[tuple[Quest, bool]]:
    """Невыполненные квесты ученика: (квест, ждёт ли проверки)."""
    quests = list(
        await session.scalars(
            select(Quest).where(_visible_to(student)).order_by(Quest.reward, Quest.id)
        )
    )
    statuses = await _statuses(session, student, quests)
    return [
        (q, statuses.get(q.id) == SubmissionStatus.pending)
        for q in quests
        if statuses.get(q.id) != SubmissionStatus.approved
    ]


async def get_open_quest(session: AsyncSession, student: User, quest_id: int) -> Quest:
    """Квест, который ученик может выполнить прямо сейчас."""
    quest = await session.scalar(
        select(Quest).where(Quest.id == quest_id, _visible_to(student))
    )
    if quest is None:
        raise QuestError("Квест не найден или уже закрыт.")
    status = (await _statuses(session, student, [quest])).get(quest_id)
    if status == SubmissionStatus.approved:
        if quest.repeat != QuestRepeat.none:
            raise QuestError(f"Квест уже выполнен, снова можно {NEXT_TIME[quest.repeat]}.")
        raise QuestError("Этот квест уже выполнен.")
    if status == SubmissionStatus.pending:
        raise QuestError("Этот квест уже ждёт проверки.")
    return quest


async def complete_auto(session: AsyncSession, student: User, quest_id: int) -> Quest:
    quest = await get_open_quest(session, student, quest_id)
    if quest.kind != QuestKind.auto:
        raise QuestError("Для этого квеста нужно отправить ответ на проверку.")
    session.add(
        Submission(
            quest_id=quest.id,
            user_id=student.id,
            status=SubmissionStatus.approved,
            reward=quest.reward,
        )
    )
    student.balance += quest.reward
    await session.commit()
    return quest


async def submit_answer(
    session: AsyncSession,
    student: User,
    quest_id: int,
    photo: dict | None,
    text: str | None,
    video: dict | None = None,
) -> Submission:
    """Ответ на проверку: текст, фото или видео (можно с подписью)."""
    quest = await get_open_quest(session, student, quest_id)
    if quest.kind != QuestKind.photo:
        raise QuestError("Этот квест не требует ответа на проверку.")
    text = (text or "").strip() or None
    if photo is None and video is None and text is None:
        raise QuestError("Пришлите ответ текстом, фото или видео.")
    if text is not None and len(text) > ANSWER_MAX:
        raise QuestError(f"Слишком длинный ответ: {len(text)} символов, максимум {ANSWER_MAX}.")
    submission = Submission(
        quest=quest, user=student, status=SubmissionStatus.pending,
        photo=photo,
        text=text,
        video=video,
    )
    session.add(submission)
    await session.commit()
    return submission


def teacher_reviewers_only(quest: Quest) -> set[int] | None:
    """Учителя, которым одним из учителей разрешено проверять квест.

    Назначены проверяющие — только они (например, физрук для спортивных квестов).
    Ежедневный квест с проверкой без назначенных — никто из учителей, кроме автора:
    ежедневный поток ответов идёт админу и модераторам, чтобы не засорять чаты учителей.
    None — обычные правила (автор и учителя класса).
    """
    if quest.reviewer_ids:
        return set(quest.reviewer_ids)
    if quest.repeat == QuestRepeat.daily and quest.kind == QuestKind.photo:
        return {quest.author_id}
    return None


def can_review(reviewer: User, submission: Submission, moderators: bool = False) -> bool:
    """moderators — включена ли временная проверка ответов модераторами."""
    if reviewer.role == Role.admin:
        return True
    if reviewer.role == Role.moderator:
        return moderators
    if reviewer.role != Role.teacher:
        return False
    quest = submission.quest
    only = teacher_reviewers_only(quest)
    if only is not None:
        return reviewer.id in only
    if quest.author_id == reviewer.id or quest.class_id in reviewer.teacher_class_ids:
        return True
    # Квест админа для всей школы: учитель проверяет учеников своих классов
    return (
        is_admin_quest(quest)
        and quest.class_id is None
        and submission.user.class_id in reviewer.teacher_class_ids
    )


def is_admin_quest(quest: Quest) -> bool:
    """Квест администрации школы (админа или модератора)."""
    return is_staff(quest.author)


async def _moderators(session: AsyncSession, reviewer: User) -> bool:
    return reviewer.role == Role.moderator and await settings_service.moderator_review(session)


async def pending_for(session: AsyncSession, reviewer: User) -> list[Submission]:
    rows = await session.scalars(
        select(Submission)
        .where(Submission.status == SubmissionStatus.pending)
        .order_by(Submission.id)
    )
    moderators = await _moderators(session, reviewer)
    return [s for s in rows.unique() if can_review(reviewer, s, moderators)]


def is_daily_approved(submission: Submission) -> bool:
    """Засчитанный ответ ежедневного квеста с проверкой (зарядка и т. п.).

    Их много, поэтому в общем архиве их нет: админ смотрит их отдельной кнопкой.
    """
    quest = submission.quest
    return (
        submission.status == SubmissionStatus.approved
        and quest.kind == QuestKind.photo
        and quest.repeat == QuestRepeat.daily
    )


async def sees_daily_archive(session: AsyncSession, user: User) -> bool:
    """Раздел засчитанных ответов ежедневных квестов: админ и модератор, пока он проверяет ответы."""
    return user.role == Role.admin or await _moderators(session, user)


async def daily_review_titles(session: AsyncSession) -> list[str]:
    """Названия ежедневных квестов с проверкой — для кнопки в архиве админа."""
    rows = await session.scalars(
        select(Quest.title)
        .where(Quest.kind == QuestKind.photo, Quest.repeat == QuestRepeat.daily)
        .order_by(Quest.id)
    )
    return list(rows)


async def reviewed_for(
    session: AsyncSession, reviewer: User, offset: int, limit: int, daily: bool = False
) -> tuple[list[Submission], bool]:
    """Архив проверенных ответов, новые сверху: (страница, есть ли ещё).

    daily=True — только засчитанные ответы ежедневных квестов (отдельный раздел).
    """
    if daily and not await sees_daily_archive(session, reviewer):
        return [], False
    rows = await session.scalars(
        select(Submission)
        .where(
            Submission.status != SubmissionStatus.pending,
            or_(
                Submission.photo.is_not(None),
                Submission.text.is_not(None),
                Submission.video.is_not(None),
            ),
        )
        .order_by(Submission.reviewed_at.desc(), Submission.id.desc())
    )
    moderators = await _moderators(session, reviewer)
    visible = [
        s
        for s in rows.unique()
        if s.quest.kind == QuestKind.photo
        and can_review(reviewer, s, moderators)
        and is_daily_approved(s) == daily
    ]
    return visible[offset : offset + limit], len(visible) > offset + limit


async def get_reviewed(
    session: AsyncSession, reviewer: User, submission_id: int
) -> Submission:
    submission = await session.get(Submission, submission_id)
    if (
        submission is None
        or submission.status == SubmissionStatus.pending
        or not can_review(reviewer, submission, await _moderators(session, reviewer))
        or (is_daily_approved(submission) and not await sees_daily_archive(session, reviewer))
    ):
        raise QuestError("Заявка не найдена.")
    return submission


async def review(
    session: AsyncSession,
    reviewer: User,
    submission_id: int,
    approve: bool,
    amount: int | None = None,
    comment: str | None = None,
) -> Submission:
    """amount — сумма для квеста с диапазоном награды; comment — причина отклонения."""
    submission = await session.get(Submission, submission_id)
    moderators = await _moderators(session, reviewer)
    if submission is None or not can_review(reviewer, submission, moderators):
        raise QuestError("Заявка не найдена.")
    if submission.status != SubmissionStatus.pending:
        raise QuestError("Эта заявка уже проверена.")
    quest = submission.quest
    if approve:
        if quest.reward_min is None:
            amount = quest.reward
        elif amount is None or not quest.reward_min <= amount <= quest.reward:
            raise QuestError(
                f"Выберите сумму от {quest.reward_min} до {quest.reward} кнопкой под ответом."
            )
    submission.status = SubmissionStatus.approved if approve else SubmissionStatus.rejected
    submission.reviewer = reviewer
    submission.reviewed_at = datetime.now(timezone.utc)
    comment = (comment or "").strip()
    submission.review_comment = comment[:REASON_MAX] if comment and not approve else None
    if approve:
        submission.reward = amount
        submission.user.balance += amount
    await session.commit()
    return submission


async def override(
    session: AsyncSession, admin: User, submission_id: int
) -> tuple[Submission, User | None, int]:
    """Админ меняет решение по проверенной заявке на противоположное.

    Возвращает (заявка, кто проверял до этого, изменение баланса ученика).
    Монеты при отмене списываются, но не ниже нуля: ученик мог их уже потратить.
    """
    if admin.role != Role.admin:
        raise QuestError("Менять решения может только админ.")
    submission = await get_reviewed(session, admin, submission_id)
    if submission.quest.kind != QuestKind.photo:
        raise QuestError("Это решение изменить нельзя.")
    previous = submission.reviewer
    student, quest = submission.user, submission.quest
    if submission.status == SubmissionStatus.approved:
        submission.status = SubmissionStatus.rejected
        delta = -min(submission.earned, student.balance)
    else:
        # Засчитываем: прежняя сумма, если ответ уже был засчитан, иначе минимум диапазона
        reward = submission.reward or quest.reward_min or quest.reward
        submission.status = SubmissionStatus.approved
        submission.reward = reward
        delta = reward
    student.balance += delta
    submission.reviewer = admin
    submission.reviewed_at = datetime.now(timezone.utc)
    await session.commit()
    return submission, previous, delta


async def reviewers_for(session: AsyncSession, submission: Submission) -> list[User]:
    """Кого уведомить о новой заявке.

    Админов, модераторов (пока им разрешена проверка) и из учителей — только автора
    квеста, а если у квеста назначены проверяющие — только их. Остальные учителя,
    которым можно проверить ответ, видят его в «На проверке», но без уведомления.
    """
    quest = submission.quest
    conditions = [User.role == Role.admin]
    if quest.reviewer_ids:
        conditions.append(User.id.in_(quest.reviewer_ids))
    else:
        conditions.append(User.id == quest.author_id)
    if await settings_service.moderator_review(session):
        conditions.append(User.role == Role.moderator)
    users = (await session.scalars(select(User).where(or_(*conditions)))).unique()
    # Модератор проверяет ответы, только пока админ это разрешил
    allowed = {Role.admin, Role.teacher}
    if await settings_service.moderator_review(session):
        allowed.add(Role.moderator)
    return [u for u in users if u.role in allowed]


# --- управление квестами (учитель — свои, админ — все) ---


def can_manage(user: User, quest: Quest) -> bool:
    """Админ — любой квест; учитель и модератор — только свои."""
    if user.role == Role.admin:
        return True
    return user.role in (Role.teacher, Role.moderator) and quest.author_id == user.id


async def managed_quests(
    session: AsyncSession, user: User, active: bool, offset: int, limit: int
) -> tuple[list[Quest], bool]:
    """Квесты в разделе управления, новые сверху: (страница, есть ли ещё).

    Учитель видит свои квесты и квесты админа для школы или своих классов:
    их он не закрывает, но проверяет ответы.
    """
    query = select(Quest).where(Quest.is_active.is_(active)).order_by(Quest.id.desc())
    if is_staff(user):  # модератор видит все квесты, управляет своими
        rows = list(await session.scalars(query.offset(offset).limit(limit + 1)))
        return rows[:limit], len(rows) > limit
    rows = [q for q in (await session.scalars(query)).unique() if can_view(user, q)]
    return rows[offset : offset + limit], len(rows) > offset + limit


def can_view(user: User, quest: Quest) -> bool:
    """Учитель видит все квесты, доступные ученикам его классов: свои, коллег и администрации."""
    if can_manage(user, quest) or user.role == Role.moderator:
        return True
    return user.role == Role.teacher and (
        quest.class_id is None or quest.class_id in user.teacher_class_ids
    )


async def get_managed(session: AsyncSession, user: User, quest_id: int) -> Quest:
    quest = await session.get(Quest, quest_id)
    if quest is None or not can_manage(user, quest):
        raise QuestError("Квест не найден.")
    return quest


async def get_viewable(session: AsyncSession, user: User, quest_id: int) -> Quest:
    quest = await session.get(Quest, quest_id)
    if quest is None or not can_view(user, quest):
        raise QuestError("Квест не найден.")
    return quest


async def quest_stats(session: AsyncSession, quest_id: int) -> dict[SubmissionStatus, int]:
    """Сколько заявок в каждом статусе. approved = сколько учеников выполнили."""
    rows = await session.execute(
        select(Submission.status, func.count())
        .where(Submission.quest_id == quest_id)
        .group_by(Submission.status)
    )
    return {status: count for status, count in rows}


EDITABLE = ("title", "description", "reward", "repeat", "photo")


async def update_quest(
    session: AsyncSession, user: User, quest_id: int, field: str, value
) -> Quest:
    """Изменить одно поле квеста. Учитель и модератор — награду в пределах FIXED_RANGES."""
    quest = await get_managed(session, user, quest_id)
    if field == "title":
        value = (value or "").strip()
        if not value or len(value) > TITLE_MAX:
            raise QuestError(f"Название: от 1 до {TITLE_MAX} символов.")
    elif field == "description":
        value = (value or "").strip()
        if not value or len(value) > DESCRIPTION_MAX:
            raise QuestError(f"Описание: от 1 до {DESCRIPTION_MAX} символов.")
    elif field == "reward":
        low, value = parse_reward_range(user, str(value))
        _check_range(quest.kind, low)
        quest.reward_min = low
    elif field == "repeat":
        value = QuestRepeat(value)
    elif field != "photo":
        raise QuestError("Это поле изменить нельзя.")
    setattr(quest, field, value)
    if field == "title":
        # Названия серий ежедневного квеста повторяют название квеста
        await ach_service.rename_streaks(session, quest)
    await session.commit()
    return quest


async def set_active(
    session: AsyncSession, user: User, quest_id: int, active: bool
) -> Quest:
    """Закрыть или снова открыть квест.

    Закрытый квест пропадает у учеников, но уже отправленные заявки
    можно рассмотреть: работа ученика не должна пропасть.
    """
    quest = await get_managed(session, user, quest_id)
    if quest.approval is not None:
        raise QuestError("Квест ещё не подтверждён админом.")
    if quest.is_active == active:
        raise QuestError("Квест уже открыт." if active else "Квест уже закрыт.")
    quest.is_active = active
    await session.commit()
    return quest


async def students_for(session: AsyncSession, quest: Quest) -> list[User]:
    """Зарегистрированные ученики, которым доступен квест."""
    query = select(User).where(User.role == Role.student, User.full_name.is_not(None))
    if quest.class_id is not None:
        query = query.where(User.class_id == quest.class_id)
    return list((await session.scalars(query)).unique())


async def toggle_reviewer(
    session: AsyncSession, user: User, quest_id: int, teacher_id: int
) -> Quest:
    """Назначить учителя проверяющим квеста или снять."""
    quest = await get_managed(session, user, quest_id)
    teacher = await session.get(User, teacher_id)
    if teacher is None or teacher.role != Role.teacher:
        raise QuestError("Учитель не найден.")
    ids = list(quest.reviewer_ids or [])
    ids.remove(teacher_id) if teacher_id in ids else ids.append(teacher_id)
    quest.reviewer_ids = ids or None  # новый список — чтобы изменение JSON сохранилось
    await session.commit()
    return quest


async def reviewer_names(session: AsyncSession, quest: Quest) -> str | None:
    if not quest.reviewer_ids:
        return None
    users = await session.scalars(
        select(User).where(User.id.in_(quest.reviewer_ids)).order_by(User.full_name)
    )
    return ", ".join(u.display_name for u in users.unique()) or None
