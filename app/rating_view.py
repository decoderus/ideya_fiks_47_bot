"""Рейтинг школы и классов."""
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app import keyboards as kb
from app.db.models import Role, User
from app.keyboards import coins
from app.services import classes as classes_service
from app.services import contests as contests_service
from app.services import rating as rating_service
from app.services import seasons as seasons_service
from app.ui import Screen, parse_id

log = logging.getLogger(__name__)

RATING_TOP = 10
MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}


class RatingMixin:
    """Часть класса Bot: рейтинг."""

    async def _rating(
        self, session: AsyncSession, user: User, class_id: int | None
    ) -> Screen | str:
        """class_id=None — рейтинг школы. Ученику доступны школа и свой класс."""
        all_classes = await classes_service.all_classes(session)
        if user.role == Role.student:
            visible = [c for c in all_classes if c.id == user.class_id]
        else:
            visible = all_classes
        if class_id is not None:
            school_class = next((c for c in visible if c.id == class_id), None)
            if school_class is None:
                return "Доступен рейтинг только вашего класса."
            title = f"🏆 Рейтинг класса «{school_class.name}»"
            badges = (await contests_service.badges(session)).get(school_class.id)
            if badges:
                title += f" · {', '.join(badges)}"
        else:
            title = "🏆 Рейтинг школы"

        entries = await rating_service.leaderboard(session, class_id)
        season = await seasons_service.current(session)
        lines = [title, "По текущему балансу монет"]
        if season.season is not None and season.next_start is not None:
            lines.append(
                f"Итоги «{season.season.title}» — {season.next_start:%d.%m} в 8:00, "
                "призёры — по балансу на этот момент"
            )
        lines.append("")
        if not entries:
            lines.append("Пока здесь никого нет.")
        def row(e: rating_service.Entry) -> str:
            me = " ← вы" if e.user_id == user.id else ""
            return f"{MEDALS.get(e.place, f'{e.place}.')} {e.name} — {e.score}{me}"

        top = entries[:RATING_TOP]
        for i, e in enumerate(top):
            # Черта отделяет призёров (с учётом ничьих) от остальных
            if i and e.place > 3 >= top[i - 1].place:
                lines.append("―――――――――――")
            lines.append(row(e))
        mine = next((e for e in entries if e.user_id == user.id), None)
        if mine:
            if mine not in top:
                lines += ["…", row(mine)]
            lines += ["", f"Ваше место: {mine.place} из {len(entries)} ({coins(mine.score)})"]
        lines += await self._contest_brief(session, user)
        return "\n".join(lines), kb.rating_nav(
            visible, class_id, await contests_service.badges(session),
            by_classes=user.role != Role.student,
        )

    async def _rating_classes(self, session: AsyncSession, user: User) -> Screen | str:
        if user.role == Role.student:
            return "Доступен рейтинг только вашего класса."
        classes = await classes_service.all_classes(session)
        if not classes:
            return "Классов пока нет."
        return (
            "🏆 Рейтинг по классам\nВыберите класс:",
            kb.rating_classes(classes, await contests_service.badges(session)),
        )

    # --- маршрут раздела ---

    async def _rating_callback(self, session: AsyncSession, user: User, payload: str) -> Screen | str:
        if payload in ("menu:rating", "rate:school"):
            return await self._rating(session, user, None)
        if payload == "rate:contest":
            return await self._contest(session, user)
        if payload == "rate:classes":
            return await self._rating_classes(session, user)
        if payload.startswith("rate:class:"):
            return await self._rating(session, user, parse_id(payload))
        return "Неизвестное действие."
