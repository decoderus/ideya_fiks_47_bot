"""Регистрация: роль → ФИО → класс ученика или заявка учителя.

Ученик зачисляется в выбранный класс сразу. Учителя класса получают уведомление,
админ видит зачисление в журнале «🆕 Регистрации». Зачисление можно отменить:
удалить ученика или перевести его в другой класс.
Учитель ждёт, пока админ подтвердит роль и назначит классы.
"""
import logging

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app import keyboards as kb
from app.db.models import Role, User
from app.services import classes as classes_service
from app.services import teachers as teachers_service
from app.services import users as users_service
from app.services.classes import ClassError
from app.ui import Screen, parse_id, parse_ids

log = logging.getLogger(__name__)

ST_REG_ROLE = "reg_role"
ST_REG_TEACHER = "reg_teacher"  # предупреждение о заявке на роль учителя
ST_REG_NAME = "reg_name"
ST_REG_CLASS = "reg_class"
ST_REG_TEACHER_CLASSES = "reg_teacher_classes"  # учитель отмечает свои классы

WELCOME = "Добро пожаловать в Сферум. Актив!"
ASK_NAME = "Напишите, пожалуйста, вашу фамилию и имя, например: Иванов Иван"
BAD_NAME = (
    "Не получилось распознать. Напишите фамилию и имя через пробел, "
    "только буквами, например: Иванов Иван"
)
TEACHER_WARNING = (
    "👩‍🏫 Роль учителя\n\n"
    "Вы укажете фамилию, имя и классы, в которых работаете. Администратор проверит "
    "заявку и подтвердит роль. До этого бот будет недоступен.\n\n"
    "Подать заявку?"
)
WAITING = (
    "⏳ Заявка на роль учителя на рассмотрении.\n"
    "Когда администратор подтвердит её, придёт сообщение со списком ваших классов."
)
TEACHER_CLASSES = (
    "Отметьте классы, в которых вы работаете (можно несколько), "
    "и нажмите «Отправить заявку». Администратор проверит выбор."
)


class RegistrationMixin:
    """Часть класса Bot: экраны регистрации и проверка зачислений учителями."""

    # --- экраны ---

    @staticmethod
    def _reg_role_screen(role: str) -> Screen:
        return f"{WELCOME}\nКто вы? Выберите роль и нажмите «Продолжить».", kb.reg_roles(role)

    async def _reg_categories_screen(self, session: AsyncSession) -> Screen:
        groups = await classes_service.categories(session)
        if not groups:
            return (
                "Классы ещё не созданы. Обратитесь к администратору и попробуйте позже.",
                kb.reg_retry(),
            )
        return "В каком вы классе? Сначала выберите группу:", kb.reg_categories(groups)

    async def _registration_screen(self, session: AsyncSession, user: User) -> Screen:
        """Текущий шаг регистрации: после перезапуска, на любой текст или кнопку."""
        if user.role in (Role.admin, Role.moderator):
            await users_service.set_state(session, user, ST_REG_NAME, {"role": user.role.value})
            return f"{WELCOME}\n{ASK_NAME}", None
        if await teachers_service.pending_request(session, user) is not None:
            return WAITING, kb.reg_waiting()
        if user.state == ST_REG_TEACHER:
            return TEACHER_WARNING, kb.reg_teacher_confirm()
        if user.state == ST_REG_NAME:
            return ASK_NAME, kb.reg_back()
        if user.state == ST_REG_CLASS and (user.state_data or {}).get("full_name"):
            return await self._reg_categories_screen(session)
        if user.state == ST_REG_TEACHER_CLASSES and (user.state_data or {}).get("full_name"):
            return await self._reg_teacher_classes_screen(session, user)
        role = (user.state_data or {}).get("role", "student") if user.state == ST_REG_ROLE else "student"
        await users_service.set_state(session, user, ST_REG_ROLE, {"role": role})
        return self._reg_role_screen(role)

    async def _reg_teacher_classes_screen(self, session: AsyncSession, user: User) -> Screen:
        classes = await classes_service.all_classes(session)
        chosen = set((user.state_data or {}).get("classes", []))
        if not classes:
            return (
                "Классы в боте ещё не созданы. Отправьте заявку — классы вам назначит "
                "администратор.",
                kb.reg_teacher_classes([], set()),
            )
        names = ", ".join(c.name for c in classes if c.id in chosen) or "пока не выбраны"
        return f"{TEACHER_CLASSES}\n\nВыбрано: {names}", kb.reg_teacher_classes(classes, chosen)

    # --- текст: только ФИО ---

    async def _registration_text(self, session: AsyncSession, user: User, text: str) -> Screen:
        if user.state != ST_REG_NAME or not text or text.startswith("/"):
            return await self._registration_screen(session, user)
        full_name = users_service.normalize_full_name(text)
        role = (user.state_data or {}).get("role", "student")
        if full_name is None:
            return BAD_NAME, None if user.role in (Role.admin, Role.moderator) else kb.reg_back()
        if user.role in (Role.admin, Role.moderator):
            await users_service.register(session, user, full_name)
            return self._menu(user, "Спасибо, регистрация завершена.")
        if role == "teacher":
            await users_service.set_state(
                session, user, ST_REG_TEACHER_CLASSES,
                {"role": "teacher", "full_name": full_name, "classes": []},
            )
            return await self._reg_teacher_classes_screen(session, user)
        await users_service.set_state(
            session, user, ST_REG_CLASS, {"role": "student", "full_name": full_name}
        )
        return await self._reg_categories_screen(session)

    # --- кнопки ---

    async def _registration_callback(
        self, session: AsyncSession, user: User, payload: str
    ) -> Screen | str:
        if user.role in (Role.admin, Role.moderator) or not payload.startswith("reg:"):
            return await self._registration_screen(session, user)
        if payload == "reg:withdraw":
            await teachers_service.withdraw(session, user)
            await users_service.set_state(session, user, None)
            return await self._registration_screen(session, user)
        if await teachers_service.pending_request(session, user) is not None:
            return WAITING, kb.reg_waiting()

        data = user.state_data or {}
        if payload in ("reg:back", "reg:restart"):
            role = data.get("role", "student") if payload == "reg:back" else "student"
            await users_service.set_state(session, user, ST_REG_ROLE, {"role": role})
            return self._reg_role_screen(role)
        if payload.startswith("reg:role:"):
            role = "teacher" if payload == "reg:role:teacher" else "student"
            await users_service.set_state(session, user, ST_REG_ROLE, {"role": role})
            return self._reg_role_screen(role)
        if payload == "reg:next":
            role = data.get("role", "student") if user.state == ST_REG_ROLE else "student"
            if role == "teacher":
                await users_service.set_state(session, user, ST_REG_TEACHER, {"role": "teacher"})
                return TEACHER_WARNING, kb.reg_teacher_confirm()
            await users_service.set_state(session, user, ST_REG_NAME, {"role": "student"})
            return ASK_NAME, kb.reg_back()
        if payload == "reg:teacher_ok":
            if user.state != ST_REG_TEACHER:
                return await self._registration_screen(session, user)
            await users_service.set_state(session, user, ST_REG_NAME, {"role": "teacher"})
            return ASK_NAME, kb.reg_back()

        # Учитель отмечает свои классы и отправляет заявку
        if payload.startswith("reg:tcl"):
            if user.state != ST_REG_TEACHER_CLASSES or not data.get("full_name"):
                return await self._registration_screen(session, user)
            chosen = list(data.get("classes", []))
            if payload == "reg:tcl_done":
                if not chosen and await classes_service.all_classes(session):
                    return "Отметьте хотя бы один класс."
                request = await teachers_service.create_request(
                    session, user, data["full_name"], chosen
                )
                await self._notify_teacher_request(session, request)
                return f"📨 Заявка отправлена.\n\n{WAITING}", kb.reg_waiting()
            class_id = parse_id(payload) or 0
            chosen.remove(class_id) if class_id in chosen else chosen.append(class_id)
            await users_service.set_state(
                session, user, ST_REG_TEACHER_CLASSES, dict(data, classes=chosen)
            )
            return await self._reg_teacher_classes_screen(session, user)

        # Дальше — выбор класса: ФИО уже введено
        if user.state != ST_REG_CLASS or not data.get("full_name"):
            return await self._registration_screen(session, user)
        if payload == "reg:cats":
            return await self._reg_categories_screen(session)
        if payload.startswith("reg:cat:"):
            classes = await classes_service.in_category(session, payload.split(":", 2)[2])
            if not classes:
                return await self._reg_categories_screen(session)
            return "Выберите класс:", kb.reg_classes(classes)
        if payload.startswith("reg:class:"):
            try:
                school_class = await classes_service.get(session, parse_id(payload) or 0)
            except ClassError as exc:
                return str(exc)
            await users_service.enroll(session, user, data["full_name"], school_class)
            await self._notify_enrollment(session, user)
            return self._menu(user, f"🎉 Готово! Вы зачислены в класс {school_class.name}.")
        return await self._registration_screen(session, user)

    # --- уведомления ---

    async def _notify(self, users: list[User], text: str, attachments: list[dict]) -> None:
        for u in users:
            try:
                await self._send(u.max_user_id, text, attachments)
            except httpx.HTTPError:
                log.warning("Не удалось отправить уведомление %s", u.max_user_id)

    async def _notify_teacher_request(self, session: AsyncSession, request) -> None:
        names = await teachers_service.class_names(session, request.class_ids)
        await self._notify(
            await users_service.admins(session) + await users_service.moderators(session),
            f"👩‍🏫 Заявка на роль учителя: {request.full_name}\n"
            f"Классы, которые выбрал учитель: {names}\n"
            "Подтвердите, при необходимости измените классы, или отклоните заявку.",
            kb.teacher_request_actions(request.id),
        )

    async def _notify_enrollment(self, session: AsyncSession, student: User) -> None:
        school_class = student.school_class
        # Админ видит зачисления в журнале «🆕 Регистрации», уведомления получают только учителя
        text = (
            f"🆕 Новый ученик: {student.display_name}, класс {school_class.name}.\n"
            "Зачислен автоматически. Проверьте и при необходимости отмените: "
            "удалите ученика или попросите администратора перевести его в другой класс."
        )
        await self._notify(
            await classes_service.teachers(session, school_class.id),
            text,
            kb.enrollment_teacher_actions(student.id, school_class.id),
        )

    async def _notify_removed(self, student: User) -> None:
        await self._notify(
            [student],
            "Ваша регистрация отменена учителем или администратором. "
            "Если это ошибка, зарегистрируйтесь заново.",
            kb.reg_restart(),
        )

    # --- действия учителя по уведомлению о зачислении ---

    async def _enrollment(self, session: AsyncSession, user: User, payload: str) -> Screen | str:
        ids = parse_ids(payload, 2)
        if ids is None:
            return "Неизвестное действие."
        student_id, class_id = ids
        if user.role != Role.admin and class_id not in user.teacher_class_ids:
            return "Это ученик не вашего класса."
        student = await session.get(User, student_id)
        if (
            student is None
            or student.role != Role.student
            or student.full_name is None
            or student.class_id != class_id
        ):
            return "Ученик уже удалён или переведён в другой класс."
        if payload.startswith("enr:del:"):
            return (
                f"Удалить ученика {student.display_name} ({student.school_class.name})?\n"
                "Регистрация будет отменена, монеты обнулятся.",
                kb.enrollment_delete_confirm(student.id, class_id),
            )
        if payload.startswith("enr:del_ok:"):
            name = student.display_name
            await users_service.remove_student(session, student)
            await self._notify_removed(student)
            return f"🗑 Регистрация ученика {name} отменена.", kb.back_to_menu()
        if payload.startswith("enr:mv:"):
            await self._notify(
                await users_service.admins(session),
                f"🔁 {user.display_name} просит перевести ученика {student.display_name} "
                f"(сейчас {student.school_class.name}) в другой класс.",
                kb.student_admin_actions(student.id),
            )
            return "Запрос на перевод отправлен администратору."
        return "Неизвестное действие."
