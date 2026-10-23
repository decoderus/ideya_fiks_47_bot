"""Журналы для админа: движение монет по школе и действия персонала.

Движение монет собирается из базы: засчитанные квесты, ручные начисления и списания,
бонусы (достижения, оповещения, соревнования), покупки и колесо фортуны.
Действия персонала — из файла журнала (services/audit.py).
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    CoinTransaction,
    Order,
    OrderStatus,
    QuestKind,
    RewardKind,
    Submission,
    SubmissionStatus,
    User,
)
from app.services import audit

MSK = timezone(timedelta(hours=3))
# Сколько последних событий собирать в ленту (чтобы экран не тормозил на большой базе)
FEED_LIMIT = 3000

ACTIONS = {
    "role.set": "роль",
    "teacher.approve": "подтвердил(а) учителя",
    "teacher.reject": "отклонил(а) заявку учителя",
    "teacher.undo": "отменил(а) подтверждение учителя",
    "teacher.class_add": "назначил(а) класс учителю",
    "teacher.class_remove": "снял(а) класс с учителя",
    "student.transfer": "перевёл(а) ученика",
    "student.remove": "удалил(а) ученика",
    "student.reset": "обнулил(а) статистику",
    "user.rename": "переименовал(а) пользователя",
    "quest.create": "создал(а) квест",
    "quest.edit": "изменил(а) квест",
    "quest.close": "закрыл(а) квест",
    "quest.reopen": "открыл(а) квест",
    "quest.approve": "опубликовал(а) квест модератора",
    "quest.rework": "вернул(а) квест на доработку",
    "quest.resubmit": "снова отправил(а) квест на подтверждение",
    "order.issue": "выдал(а) награду",
    "order.cancel": "отменил(а) заказ",
    "broadcast.draft": "подготовил(а) оповещение",
    "broadcast.approve": "отправил(а) оповещение модератора",
    "broadcast.reject": "отклонил(а) оповещение модератора",
    "submission.approve": "засчитал(а) ответ",
    "submission.reject": "отклонил(а) ответ",
    "settings.moderator_review": "проверка ответов модераторами",
    "reward.price": "изменил(а) цены наград",
    "question.answer": "ответил(а) на вопрос ученика",
}


@dataclass
class Entry:
    at: datetime
    text: str


def _utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _when(moment: datetime) -> str:
    return _utc(moment).astimezone(MSK).strftime("%d.%m %H:%M")


def _who(user: User | None) -> str:
    if user is None:
        return "—"
    name = user.display_name
    if user.school_class:
        name += f" ({user.school_class.name})"
    return name


async def coin_feed(session: AsyncSession) -> list[Entry]:
    """Все изменения балансов учеников, новые сверху."""
    entries: list[Entry] = []
    submissions = await session.scalars(
        select(Submission)
        .where(Submission.status == SubmissionStatus.approved)
        .order_by(Submission.id.desc())
        .limit(FEED_LIMIT)
    )
    for s in submissions.unique():
        quest = s.quest
        if quest.kind == QuestKind.quiz:
            how = "верный ответ на вопрос дня"
        elif quest.kind == QuestKind.auto:
            how = "выполнен (автоподтверждение)"
        else:
            reviewer = s.reviewer.display_name if s.reviewer else "—"
            how = f"засчитал(а) {reviewer}"
        moment = s.reviewed_at or s.created_at
        entries.append(Entry(_utc(moment), f"{_when(moment)} {_who(s.user)}: +{s.earned} — «{quest.title}», {how}"))

    transactions = await session.scalars(
        select(CoinTransaction).order_by(CoinTransaction.id.desc()).limit(FEED_LIMIT)
    )
    users: dict[int, User] = {}
    for t in transactions.unique():
        if t.user_id not in users:
            users[t.user_id] = await session.get(User, t.user_id)
        student = users[t.user_id]
        sign = "+" if t.amount > 0 else "−"
        source = "" if t.actor_id == t.user_id else f", от {t.actor.display_name}"
        entries.append(
            Entry(_utc(t.created_at), f"{_when(t.created_at)} {_who(student)}: {sign}{abs(t.amount)} — {t.reason}{source}")
        )

    orders = await session.scalars(select(Order).order_by(Order.id.desc()).limit(FEED_LIMIT))
    for o in orders.unique():
        title = o.reward.title
        if o.reward.kind == RewardKind.lottery:
            won = o.prize_won or 0
            result = (
                f"выигрыш +{won}" if won > 0
                else f"критический провал −{-won}" if won < 0
                else "без выигрыша"
            )
            text = f"−{o.price} — «{title}», {result}"
        elif o.status == OrderStatus.cancelled:
            text = f"−{o.price} и возврат — «{title}», заказ отменён"
        else:
            text = f"−{o.price} — покупка «{title}»"
        entries.append(Entry(_utc(o.created_at), f"{_when(o.created_at)} {_who(o.user)}: {text}"))

    entries.sort(key=lambda e: e.at, reverse=True)
    return entries


def _audit_files() -> list[Path]:
    """Файл журнала и его архивы (audit.log, audit.log.1 …), от свежих к старым."""
    handler = next((h for h in audit.log.handlers if isinstance(h, RotatingFileHandler)), None)
    if handler is None:
        return []
    base = Path(handler.baseFilename)
    files = [base] + [Path(f"{base}.{i}") for i in range(1, handler.backupCount + 1)]
    return [f for f in files if f.exists()]


def staff_feed() -> list[Entry] | None:
    """Действия админа и модераторов, новые сверху. None — журнал не настроен."""
    files = _audit_files()
    if not files:
        return None
    entries: list[Entry] = []
    for path in files:
        for line in reversed(path.read_text(encoding="utf-8").splitlines()):
            parts = line.split(" | ")
            if len(parts) < 6:
                continue
            stamp, who, role, _, action, details = parts[0], parts[1], parts[2], parts[3], parts[4], " | ".join(parts[5:])
            try:
                at = datetime.strptime(stamp.replace(" МСК", ""), "%Y-%m-%d %H:%M:%S").replace(tzinfo=MSK)
            except ValueError:
                continue
            label = ACTIONS.get(action, action)
            entries.append(Entry(at, f"{at:%d.%m %H:%M} {who} ({role}) — {label}: {details}"))
    return entries
