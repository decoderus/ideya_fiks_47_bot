"""Раздел «📒 Журнал успехов»: учитель смотрит учеников своих классов, админ — всех."""
from sqlalchemy.ext.asyncio import AsyncSession

from app import keyboards as kb
from app.db.models import Role, SchoolClass, User
from app.keyboards import coins
from app.services import classes as classes_service
from app.services import journal as journal_service
from app.services.journal import JournalError
from app.ui import Screen, format_day, parse_id


class JournalMixin:
    """Часть класса Bot: журнал успехов (классы → ученики → карточка ученика)."""

    async def _journal(self, session: AsyncSession, user: User, payload: str) -> Screen | str:
        if user.role not in (Role.teacher, Role.admin, Role.moderator):
            return "Раздел доступен учителям, модераторам и админам."
        if payload == "menu:journal":
            return await self._journal_classes(session, user)
        if payload.startswith("jr:cls:"):
            # jr:cls:<класс>:<страница>
            try:
                _, _, class_id, page = payload.split(":")
                class_id, page = int(class_id), max(int(page), 0)
            except ValueError:
                return "Неизвестное действие."
            if not journal_service.can_view_class(user, class_id):
                return "Доступны только ваши классы."
            return await self._journal_students(session, user, class_id, page)
        if payload.startswith("jr:st:"):
            try:
                student = await journal_service.get_student(session, user, parse_id(payload) or 0)
            except JournalError as exc:
                return str(exc)
            return await self._journal_card(session, student)
        return "Неизвестное действие."

    async def _journal_classes(self, session: AsyncSession, user: User) -> Screen | str:
        if user.role in (Role.admin, Role.moderator):
            classes = await classes_service.all_classes(session)
            if not classes:
                return "📒 Классов пока нет.", kb.back_to_menu()
        else:
            classes = user.teacher_classes
            if not classes:
                return "Вам ещё не назначены классы. Обратитесь к администратору."
            if len(classes) == 1:
                return await self._journal_students(session, user, classes[0].id, 0)
        return "📒 Журнал успехов\nВыберите класс:", kb.journal_classes(classes)

    async def _journal_students(
        self, session: AsyncSession, user: User, class_id: int, page: int
    ) -> Screen | str:
        school_class = await session.get(SchoolClass, class_id)
        if school_class is None:
            return "Класс не найден."
        students = await classes_service.students(session, class_id)
        start = page * kb.JOURNAL_PAGE
        items = students[start : start + kb.JOURNAL_PAGE]
        done = await journal_service.quests_done(session, [s.id for s in items])
        lines = [f"📒 Журнал успехов · класс «{school_class.name}»"]
        if students:
            lines.append("🎯 — выполнено квестов, 💰 — баланс.\nВыберите ученика:")
        else:
            lines.append("В классе пока нет учеников.")
        return "\n".join(lines), kb.journal_students(
            items,
            done,
            class_id,
            page,
            len(students) > start + kb.JOURNAL_PAGE,
            user.role in (Role.admin, Role.moderator) or len(user.teacher_classes) > 1,
        )

    async def _journal_card(self, session: AsyncSession, student: User) -> Screen:
        info = await journal_service.summary(session, student)
        lines = [f"📒 {student.display_name}"]
        if student.school_class:
            lines.append(f"Класс: {student.school_class.name}")
        lines += [
            "",
            f"💰 Баланс: {coins(student.balance)}",
            f"📈 Заработано всего: {coins(info.earned)}",
            f"🎯 Квестов выполнено: {info.quests_done}",
            f"🎁 Покупок в магазине: {info.purchases}",
        ]
        if info.events:
            lines += ["", f"Последние события (до {journal_service.HISTORY_LIMIT}):"]
            lines += [f"{format_day(e.at)} {e.text}" for e in info.events]
        else:
            lines += ["", "Пока никаких событий: ученик ещё не выполнял квесты."]
        return "\n".join(lines), kb.journal_card(student)
