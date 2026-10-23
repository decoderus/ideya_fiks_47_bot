"""Управление школой (админ): классы, ученики классов, учителя и их заявки."""
from sqlalchemy.ext.asyncio import AsyncSession

from app import keyboards as kb
from app.db.models import Role, User
from app.keyboards import coins
from app.services import classes as classes_service
from app.services import journal as journal_service
from app.services import school_stats
from app.services import teachers as teachers_service
from app.services import activity as activity_service
from app.services import audit
from app.services import users as users_service
from app.services.classes import ClassError
from app.services.journal import JournalError
from app.services.teachers import TeacherError
from app.ui import Screen, format_date, format_day, parse_id, parse_ids

ST_CLS_ADD = "cls_add"
ST_CLS_RENAME = "cls_rename"
CLASS_STATES = (ST_CLS_ADD, ST_CLS_RENAME)


class SchoolAdminMixin:
    """Часть класса Bot: разделы «🏫 Классы» и «👩‍🏫 Учителя»."""

    # --- классы ---

    async def _classes(self, session: AsyncSession, user: User, payload: str) -> Screen | str:
        if user.role == Role.teacher:
            return await self._teacher_classes(session, user, payload)
        if user.role == Role.moderator:
            return await self._moderator_classes(session, user, payload)
        if user.role != Role.admin:
            return "Раздел доступен только админам."
        try:
            if payload == "menu:classes":
                return await self._classes_overview(session)
            if payload == "cls:add":
                await users_service.set_state(session, user, ST_CLS_ADD)
                return (
                    "➕ Напишите классы через пробел или запятую, например: 5А 5Б 6А\n"
                    "Параллели с 1 по 11.",
                    kb.cancel(),
                )
            if payload.startswith("cls:open:"):
                return await self._class_card(session, parse_id(payload) or 0)
            if payload.startswith("cls:ren:"):
                school_class = await classes_service.get(session, parse_id(payload) or 0)
                await users_service.set_state(
                    session, user, ST_CLS_RENAME, {"class_id": school_class.id}
                )
                return f"✏️ Новое название для класса {school_class.name}:", kb.cancel()
            if payload.startswith("cls:rm:"):
                school_class = await classes_service.get(session, parse_id(payload) or 0)
                return (
                    f"Удалить класс {school_class.name}? Удалить можно только пустой класс.",
                    kb.class_delete_confirm(school_class.id),
                )
            if payload.startswith("cls:rm_ok:"):
                name = await classes_service.delete(session, parse_id(payload) or 0)
                return await self._classes_overview(session, f"🗑 Класс {name} удалён.")
            if payload.startswith("cls:st:"):
                ids = parse_ids(payload, 2)
                if ids is None:
                    return "Неизвестное действие."
                return await self._class_students(session, ids[0], max(ids[1], 0))
            if payload.startswith("cls:hist:"):
                return await self._balance_history(session, user, payload)
            if payload.startswith(("cls:stu:", "cls:mv:", "cls:del:", "cls:del_ok:", "cls:mvto:")):
                return await self._class_student(session, user, payload)
        except ClassError as exc:
            return str(exc)
        return "Неизвестное действие."

    async def _moderator_classes(
        self, session: AsyncSession, user: User, payload: str
    ) -> Screen | str:
        """Модератор: все классы, история баланса и перевод ученика в другой класс."""
        try:
            if payload == "menu:classes":
                classes = await classes_service.all_classes(session)
                return (
                    f"🏫 Классы: {len(classes)}\nВыберите класс:",
                    kb.classes_overview(classes, admin=False, show_locked=True),
                )
            if payload.startswith("cls:open:"):
                return await self._class_card(
                    session, parse_id(payload) or 0, admin=False, show_locked=True
                )
            if payload.startswith("cls:st:"):
                ids = parse_ids(payload, 2)
                if ids is None:
                    return "Неизвестное действие."
                return await self._class_students(session, ids[0], max(ids[1], 0), "moderator")
            if payload.startswith("cls:hist:"):
                return await self._balance_history(session, user, payload)
            if payload.startswith(("cls:stu:", "cls:mv:", "cls:mvto:")):
                return await self._class_student(session, user, payload)
        except ClassError as exc:
            return str(exc)
        return "Это действие доступно только админу."

    async def _teacher_classes(
        self, session: AsyncSession, user: User, payload: str
    ) -> Screen | str:
        """Учитель: свои классы, ученики и история баланса — только просмотр."""
        try:
            if payload == "menu:classes":
                classes = user.teacher_classes
                if not classes:
                    return "Вам ещё не назначены классы. Обратитесь к администратору."
                return (
                    f"🏫 Мои классы: {len(classes)}\nВыберите класс:",
                    kb.classes_overview(classes, admin=False),
                )
            if payload.startswith(("cls:open:", "cls:st:")):
                ids = parse_ids(payload, 2) if payload.startswith("cls:st:") else None
                class_id = ids[0] if ids else parse_id(payload) or 0
                if class_id not in user.teacher_class_ids:
                    return "Доступны только ваши классы."
                if ids:
                    return await self._class_students(
                        session, class_id, max(ids[1], 0), "teacher"
                    )
                return await self._class_card(session, class_id, admin=False)
            if payload.startswith("cls:stu:"):
                student = await journal_service.get_student(session, user, parse_id(payload) or 0)
                return await self._student_admin_card(session, student, mode="teacher")
            if payload.startswith("cls:hist:"):
                return await self._balance_history(session, user, payload)
        except (ClassError, JournalError) as exc:
            return str(exc)
        return "Это действие доступно только админам."

    async def _balance_history(
        self, session: AsyncSession, user: User, payload: str
    ) -> Screen | str:
        # cls:hist:<ученик>:<страница>
        ids = parse_ids(payload, 2)
        if ids is None:
            return "Неизвестное действие."
        student_id, page = ids[0], max(ids[1], 0)
        if user.role == Role.admin:
            student = await self._admin_student(session, student_id)
        else:
            student = await journal_service.get_student(session, user, student_id)
        events = await journal_service.balance_history(session, student)
        start = page * kb.BALANCE_PAGE
        items = events[start : start + kb.BALANCE_PAGE]
        lines = [f"📜 История баланса: {student.display_name}", f"Сейчас: {coins(student.balance)}"]
        if page:
            lines.append(f"Страница {page + 1}")
        lines.append("")
        has_more = len(events) > start + kb.BALANCE_PAGE
        lines += [f"{format_day(e.at)} {e.text} → 💰{e.after}" for e in items]
        # Часть монет могла появиться до того, как бот стал записывать операции
        initial = events[-1].after - events[-1].delta if events else student.balance
        if not has_more and initial:
            lines.append(f"До начала учёта: 💰{initial}")
        elif not events:
            lines.append("Баланс ещё не менялся.")
        return "\n".join(lines), kb.balance_history(student.id, page, has_more)

    async def _classes_overview(self, session: AsyncSession, note: str = "") -> Screen:
        classes = await classes_service.all_classes(session)
        lines = [note, ""] if note else []
        lines.append(f"🏫 Классы: {len(classes)}")
        if not classes:
            lines.append("Классов пока нет. Добавьте их, чтобы ученики могли зарегистрироваться.")
        for key, title in await classes_service.categories(session):
            names = ", ".join(c.name for c in classes if classes_service.category_of(c) == key)
            lines.append(f"{title}: {names}")
        return "\n".join(lines), kb.classes_overview(classes)

    async def _class_card(
        self,
        session: AsyncSession,
        class_id: int,
        note: str = "",
        admin: bool = True,
        show_locked: bool = False,
    ) -> Screen:
        school_class = await classes_service.get(session, class_id)
        students = await classes_service.students(session, class_id)
        teachers = await classes_service.teachers(session, class_id)
        lines = [note, ""] if note else []
        lines += [
            f"🏫 Класс {school_class.name}",
            f"Учеников: {len(students)}",
            "Учителя: " + (", ".join(t.display_name for t in teachers) or "не назначены"),
        ]
        return "\n".join(lines), kb.class_card(class_id, admin, show_locked)

    async def _class_students(
        self, session: AsyncSession, class_id: int, page: int, mode: str = "admin"
    ) -> Screen:
        """mode: admin — всё, moderator — история и перевод, teacher — история."""
        school_class = await classes_service.get(session, class_id)
        students = await classes_service.students(session, class_id)
        start = page * kb.CLASS_STUDENTS_PAGE
        items = students[start : start + kb.CLASS_STUDENTS_PAGE]
        text = f"👥 Ученики {school_class.name}: {len(students)}"
        if not students:
            text += "\nПока никого нет."
        elif mode == "admin":
            text += "\nВыберите ученика: история баланса, перевод или удаление."
        elif mode == "moderator":
            text += "\nВыберите ученика: история баланса или перевод в другой класс."
        else:
            text += "\nВыберите ученика, чтобы посмотреть историю баланса."
        return text, kb.class_students(
            items, class_id, page, len(students) > start + kb.CLASS_STUDENTS_PAGE
        )

    async def _admin_student(self, session: AsyncSession, student_id: int) -> User:
        student = await session.get(User, student_id)
        if student is None or student.role != Role.student or student.full_name is None:
            raise ClassError("Ученик не найден: возможно, его уже удалили.")
        return student

    async def _class_student(
        self, session: AsyncSession, actor: User, payload: str
    ) -> Screen | str:
        mode = "admin" if actor.role == Role.admin else "moderator"
        if payload.startswith("cls:mvto:"):
            ids = parse_ids(payload, 2)
            if ids is None:
                return "Неизвестное действие."
            student = await self._admin_student(session, ids[0])
            school_class = await classes_service.get(session, ids[1])
            old = student.school_class.name if student.school_class else "без класса"
            await users_service.transfer(session, student, school_class)
            audit.record(
                actor, "student.transfer",
                f"{student.display_name}: {old} → {school_class.name}",
            )
            await self._notify(
                [student], f"🔁 Вы переведены в класс {school_class.name}.", kb.back_to_menu()
            )
            return await self._student_admin_card(
                session, student, f"🔁 Переведён в {school_class.name}.", mode
            )
        student = await self._admin_student(session, parse_id(payload) or 0)
        if payload.startswith("cls:stu:"):
            return await self._student_admin_card(session, student, mode=mode)
        if payload.startswith("cls:mv:"):
            current = student.school_class.name if student.school_class else "без класса"
            return (
                f"🔁 В какой класс перевести {student.display_name}? Сейчас: {current}.",
                kb.transfer_picker(
                    student.id, await classes_service.all_classes(session), student.class_id
                ),
            )
        if payload.startswith("cls:del:"):
            return (
                f"Удалить ученика {student.display_name}?\n"
                "Регистрация будет отменена, монеты обнулятся.",
                kb.student_delete_confirm(student.id),
            )
        # cls:del_ok
        name, class_id = student.display_name, student.class_id
        await users_service.remove_student(session, student)
        audit.record(actor, "student.remove", name)
        await self._notify_removed(student)
        note = f"🗑 Регистрация ученика {name} отменена."
        if class_id is not None:
            return await self._class_card(session, class_id, note)
        return note, kb.to_classes()

    async def _student_admin_card(
        self, session: AsyncSession, student: User, note: str = "", mode: str = "admin"
    ) -> Screen:
        """mode: admin — перевод и удаление, moderator — перевод, teacher — только история."""
        lines = [note, ""] if note else []
        lines += [
            f"🎒 {student.display_name}",
            f"Класс: {student.school_class.name if student.school_class else 'не назначен'}",
            f"Баланс: {coins(student.balance)}",
        ]
        return "\n".join(lines), kb.student_admin_card(
            student,
            can_transfer=mode in ("admin", "moderator"),
            can_delete=mode == "admin",
            show_locked=mode == "moderator",
        )

    async def _class_step(self, session: AsyncSession, user: User, text: str) -> Screen:
        if not text or text.startswith("/"):
            return "Пришлите ответ текстом или нажмите «Отмена».", kb.cancel()
        try:
            if user.state == ST_CLS_ADD:
                added, existing = await classes_service.add_classes(session, text)
                await users_service.set_state(session, user, None)
                parts = []
                if added:
                    parts.append("✅ Добавлены: " + ", ".join(added))
                if existing:
                    parts.append("Уже были: " + ", ".join(existing))
                return await self._classes_overview(session, "\n".join(parts))
            # ST_CLS_RENAME
            class_id = (user.state_data or {}).get("class_id", 0)
            school_class = await classes_service.rename(session, class_id, text)
            await users_service.set_state(session, user, None)
            return await self._class_card(session, class_id, f"✏️ Теперь класс называется {school_class.name}.")
        except ClassError as exc:
            return str(exc), kb.cancel()

    # --- учителя ---

    async def _teachers(self, session: AsyncSession, user: User, payload: str) -> Screen | str:
        if payload.startswith("tr:undo:"):
            return await self._teacher_undo(session, user, parse_id(payload) or 0)
        if user.role not in (Role.admin, Role.moderator):
            return "Раздел доступен админам и модераторам."
        try:
            if payload == "menu:teachers":
                return await self._teachers_overview(session)
            if payload.startswith("tr:open:"):
                request = await self._get_request(session, parse_id(payload) or 0)
                names = await teachers_service.class_names(session, request.class_ids)
                return (
                    f"👩‍🏫 Заявка на роль учителя\n{request.full_name}\n\n"
                    f"Классы, которые выбрал учитель: {names}\n"
                    "Подтвердите или сначала измените классы.",
                    kb.teacher_request_card(request.id),
                )
            if payload.startswith(("tr:edit:", "tr:t:")):
                if payload.startswith("tr:t:"):
                    ids = parse_ids(payload, 2)
                    if ids is None:
                        return "Неизвестное действие."
                    request = await teachers_service.toggle_request_class(
                        session, user, ids[0], ids[1]
                    )
                else:
                    request = await self._get_request(session, parse_id(payload) or 0)
                classes = await classes_service.all_classes(session)
                names = await teachers_service.class_names(session, request.class_ids)
                return (
                    f"✏️ Классы учителя {request.full_name}\nСейчас: {names}\n"
                    "Нажимайте на классы, чтобы добавить или убрать, затем подтвердите.",
                    kb.request_classes_editor(request.id, classes, set(request.class_ids or [])),
                )
            if payload.startswith("tr:ok:"):
                request = await teachers_service.approve(session, user, parse_id(payload) or 0)
                teacher = request.user
                names = ", ".join(c.name for c in teacher.teacher_classes) or "не назначены"
                audit.record(
                    user, "teacher.approve",
                    f"{request.full_name} (заявка №{request.id}), классы: {names}",
                )
                await self._notify(
                    [teacher],
                    f"✅ Заявка одобрена, теперь вы учитель!\n🏫 Ваши классы: {names}",
                    kb.back_to_menu(),
                )
                if user.role == Role.moderator:
                    await self._notify(
                        await users_service.admins(session),
                        f"👩‍🏫 Модератор {user.display_name} подтвердил(а) учителя: "
                        f"{request.full_name}.\nКлассы: {names}\nЕсли это ошибка — отмените.",
                        kb.teacher_approval_undo(request.id),
                    )
                return await self._teacher_editor(
                    session, teacher, f"✅ Роль подтверждена, классы назначены: {names}.\n"
                    "При необходимости измените ниже:"
                )
            if payload.startswith("tr:no:"):
                request = await teachers_service.reject(session, user, parse_id(payload) or 0)
                audit.record(user, "teacher.reject", f"{request.full_name} (заявка №{request.id})")
                if user.role == Role.moderator:
                    await self._notify(
                        await users_service.admins(session),
                        f"👩‍🏫 Модератор {user.display_name} отклонил(а) заявку учителя: "
                        f"{request.full_name}.",
                        kb.back_to_menu(),
                    )
                await self._notify(
                    [request.user],
                    "❌ Заявка на роль учителя отклонена. Можно зарегистрироваться заново.",
                    kb.reg_restart(),
                )
                return await self._teachers_overview(
                    session, f"❌ Заявка {request.full_name} отклонена."
                )
            if payload.startswith("tc:open:"):
                teacher = await teachers_service.get_teacher(session, parse_id(payload) or 0)
                return await self._teacher_editor(session, teacher)
            if payload.startswith("tc:t:"):
                ids = parse_ids(payload, 2)
                if ids is None:
                    return "Неизвестное действие."
                teacher, assigned = await teachers_service.toggle_class(
                    session, user, ids[0], ids[1]
                )
                school_class = next(
                    (c for c in await classes_service.all_classes(session) if c.id == ids[1]), None
                )
                audit.record(
                    user,
                    "teacher.class_add" if assigned else "teacher.class_remove",
                    f"{teacher.display_name}: {school_class.name if school_class else ids[1]}",
                )
                return await self._teacher_editor(session, teacher)
            if payload.startswith("tc:done:"):
                teacher = await teachers_service.get_teacher(session, parse_id(payload) or 0)
                names = ", ".join(c.name for c in teacher.teacher_classes)
                await self._notify(
                    [teacher],
                    f"🏫 Ваши классы: {names}" if names else "🏫 Классы пока не назначены.",
                    kb.back_to_menu(),
                )
                return await self._teachers_overview(
                    session, f"Учителю {teacher.display_name} отправлен список классов."
                )
        except TeacherError as exc:
            return str(exc)
        return "Неизвестное действие."

    async def _teacher_undo(self, session: AsyncSession, user: User, request_id: int) -> Screen | str:
        """Админ отменяет подтверждение учителя, сделанное модератором."""
        try:
            request = await teachers_service.undo_approval(session, user, request_id)
        except TeacherError as exc:
            return str(exc)
        audit.record(user, "teacher.undo", f"{request.full_name} (заявка №{request.id})")
        await self._notify(
            [request.user],
            "↩️ Подтверждение роли учителя отменено администратором. "
            "Если это ошибка — свяжитесь с администрацией и зарегистрируйтесь заново.",
            kb.reg_restart(),
        )
        return f"↩️ Подтверждение отменено: {request.full_name} больше не учитель.", kb.back_to_menu()

    async def _get_request(self, session: AsyncSession, request_id: int):
        for request in await teachers_service.pending_requests(session):
            if request.id == request_id:
                return request
        raise TeacherError("Заявка уже рассмотрена или отозвана.")

    async def _teachers_overview(self, session: AsyncSession, note: str = "") -> Screen:
        requests = await teachers_service.pending_requests(session)
        teachers = await teachers_service.teachers(session)
        lines = [note, ""] if note else []
        lines.append("👩‍🏫 Учителя")
        if requests:
            lines.append(f"⏳ Ждут подтверждения: {len(requests)}")
        if not teachers and not requests:
            lines.append("Учителей пока нет.")
        elif teachers:
            lines.append("Выберите учителя, чтобы изменить его классы.")
        return "\n".join(lines), kb.teachers_overview(requests, teachers)

    async def _teacher_editor(self, session: AsyncSession, teacher: User, note: str = "") -> Screen:
        classes = await classes_service.all_classes(session)
        names = ", ".join(c.name for c in teacher.teacher_classes) or "не назначены"
        lines = [note, ""] if note else []
        lines += [
            f"👩‍🏫 {teacher.display_name}",
            f"Классы: {names}",
            "Нажимайте на классы, чтобы назначить или снять, затем «Готово».",
        ]
        if not classes:
            lines.append("Сначала добавьте классы в разделе «🏫 Классы».")
        return "\n".join(lines), kb.teacher_classes_editor(
            teacher.id, classes, teacher.teacher_class_ids
        )

    # --- журнал регистраций ---

    async def _enrollments(self, session: AsyncSession, user: User, payload: str) -> Screen | str:
        if user.role not in (Role.admin, Role.moderator):
            return "Раздел доступен админам и модераторам."
        if payload == "enl:all":
            count = await school_stats.check_all(session)
            return await self._enrollment_list(session, 0, f"✅ Отмечено проверенными: {count}")
        if payload.startswith("enl:open:"):
            entry = await school_stats.check(session, parse_id(payload) or 0)
            if entry is None:
                return "Запись не найдена."
            note = (
                f"🆕 Регистрация {format_date(entry.created_at)}: "
                f"{entry.full_name}, класс {entry.class_name}."
            )
            student = await session.get(User, entry.user_id)
            if student is None or student.role != Role.student or student.full_name is None:
                return f"{note}\nУченик уже удалён или больше не ученик.", kb.to_enrollments()
            mode = "admin" if user.role == Role.admin else "moderator"
            return await self._student_admin_card(session, student, note, mode)
        return await self._enrollment_list(session, max(parse_id(payload) or 0, 0))

    async def _enrollment_list(self, session: AsyncSession, page: int, note: str = "") -> Screen:
        items, has_more = await school_stats.enrollments(
            session, page * kb.ENROLLMENT_PAGE, kb.ENROLLMENT_PAGE
        )
        unchecked = await school_stats.unchecked_count(session)
        lines = [note, ""] if note else []
        lines.append("🆕 Регистрации учеников, новые сверху")
        lines.append(f"🔵 Не проверено: {unchecked}" if unchecked else "Все проверены ✅")
        if not items:
            lines.append("Пока никто не регистрировался.")
        if page:
            lines.append(f"Страница {page + 1}")
        return "\n".join(lines), kb.enrollment_list(
            items, page, has_more, unchecked > 0, format_day
        )

    # --- статистика ---

    async def _stats(self, session: AsyncSession, user: User) -> Screen | str:
        if user.role not in (Role.admin, Role.moderator):
            return "Раздел доступен админам и модераторам."
        data = await school_stats.school(session)
        by_role = data["by_role"]
        lines = [
            "📊 Статистика",
            "",
            f"Пользователей: {data['total']}",
            f"• учеников: {by_role.get(Role.student, 0)}",
            f"• учителей: {by_role.get(Role.teacher, 0)}",
            f"• модераторов: {by_role.get(Role.moderator, 0)}",
            f"• админов: {by_role.get(Role.admin, 0)}",
            f"• не завершили регистрацию: {data['unfinished']}",
        ]
        if data["teacher_requests"]:
            lines.append(f"⏳ Заявок на роль учителя: {data['teacher_requests']}")

        lines += ["", "Ученики по классам:"]
        if not data["classes"]:
            lines.append("Классов пока нет.")
        for key, title in await classes_service.categories(session):
            group = [(c, n) for c, n in data["classes"] if classes_service.category_of(c) == key]
            lines.append(f"{title} — {sum(n for _, n in group)}")
            lines.append("  " + " · ".join(f"{c.name}: {n}" for c, n in group))

        teachers = data["teachers"]
        lines += ["", f"Учителя: {len(teachers)}"]
        for t in teachers:
            names = ", ".join(c.name for c in t.teacher_classes) or "классы не назначены"
            lines.append(f"• {t.display_name} — {names}")
        covered = {c.id for t in teachers for c in t.teacher_classes}
        without = [c.name for c, _ in data["classes"] if c.id not in covered]
        if without:
            lines.append("Классы без учителя: " + ", ".join(without))
        return "\n".join(lines), kb.stats(admin=user.role == Role.admin)

    # --- журналы (только админ) ---

    async def _logs(self, session: AsyncSession, user: User, payload: str) -> Screen | str:
        if user.role != Role.admin:
            return "🔒 Журналы доступны только админу."
        parts = payload.split(":")
        kind = parts[1] if len(parts) > 1 else "coins"
        page = max(parse_id(payload) or 0, 0)
        if kind == "staff":
            entries = activity_service.staff_feed()
            title = "🛡 Действия админа и модераторов, новые сверху"
            if entries is None:
                return f"{title}\n\nЖурнал на сервере не настроен.", kb.log_nav(kind, 0, False)
        else:
            entries = await activity_service.coin_feed(session)
            title = "💰 Движение монет учеников, новые сверху"
        start = page * kb.LOG_PAGE
        items = entries[start : start + kb.LOG_PAGE]
        lines = [title]
        if page:
            lines.append(f"Страница {page + 1}")
        lines.append("")
        if not items:
            lines.append("Записей пока нет.")
        for e in items:
            lines.append(e.text if len(e.text) <= 220 else e.text[:217] + "…")
        return "\n".join(lines), kb.log_nav(kind, page, len(entries) > start + kb.LOG_PAGE)
