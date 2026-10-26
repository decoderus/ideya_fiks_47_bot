"""Раздел «📒 Журнал успехов»: учителя и админ отмечают успехи любых учеников школы,
записи копятся в карточке ученика. Модераторы смотрят, ученик видит свои в «🌟 Мои успехи»."""
import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app import keyboards as kb
from app.db.models import Role, SchoolClass, Success, User
from app.keyboards import ROLE_TITLES, coins
from app.services import audit
from app.services import classes as classes_service
from app.services import journal as journal_service
from app.services import pulse as pulse_service
from app.services import successes as successes_service
from app.services import users as users_service
from app.services.successes import SuccessError
from app.ui import Screen, format_day, parse_id

ST_JR_CAT = "jr_cat"
ST_JR_TEXT = "jr_text"
ST_JR_COINS = "jr_coins"
JOURNAL_STATES = (ST_JR_CAT, ST_JR_TEXT, ST_JR_COINS)


def _ints(payload: str) -> list[int]:
    """«jr:cls:5:2» -> [5, 2]."""
    out = []
    for part in payload.split(":")[2:]:
        try:
            out.append(int(part))
        except ValueError:
            pass
    return out


def entry_text(success: Success, with_student: bool = False) -> str:
    who = f"{success.student.display_name}: " if with_student else ""
    lines = [
        f"{successes_service.label(success)} · {format_day(success.created_at)}",
        "",
        f"{who}{success.text}",
        "",
        f"— {success.author.display_name}, {ROLE_TITLES[success.author_role]}",
    ]
    if success.coins:
        lines.append(f"Начислено: +{coins(success.coins)}")
    return "\n".join(lines)


class JournalMixin:
    """Часть класса Bot: журнал успехов."""

    async def _journal(self, session: AsyncSession, user: User, payload: str) -> Screen | str:
        if payload.startswith("jr:my:"):
            return await self._my_successes(session, user, max((_ints(payload) or [0])[0], 0))
        if not successes_service.can_view(user):
            return "Раздел доступен учителям, модераторам и админам."
        ids = _ints(payload)
        if payload == "menu:journal":
            return await self._journal_hub(session, user)
        if payload.startswith("jr:cls:") and ids:
            return await self._class_pulse(session, user, ids[0])
        if payload.startswith("jr:all:") and ids:
            return await self._journal_students(session, ids[0], max(ids[1] if len(ids) > 1 else 0, 0))
        if payload.startswith(("jr:idle:", "jr:help:")) and ids:
            return await self._pulse_list(session, ids[0], payload.startswith("jr:idle:"))
        if payload.startswith("jr:st:") and ids:
            return await self._journal_card(session, user, ids[0], max(ids[1] if len(ids) > 1 else 0, 0))
        if payload.startswith("jr:feed:"):
            return await self._journal_feed(session, max((ids or [0])[0], 0))
        if payload.startswith("jr:e:") and ids:
            return await self._success_entry(session, user, ids[0])
        if payload.startswith("jr:delok:") and ids:
            return await self._success_delete(session, user, ids[0])
        if payload.startswith("jr:del:") and ids:
            return "Удалить запись? Начисленные с ней монеты останутся у ученика.", kb.success_delete_confirm(ids[0])
        # --- новая запись ---
        if not successes_service.can_write(user):
            return "Отмечать успехи могут учителя и администрация."
        if payload == "jr:new":
            classes = await classes_service.all_classes(session)
            return "✍️ Отметить успех\nВыберите класс ученика:", kb.success_write_classes(classes)
        if payload.startswith("jr:nc:") and ids:
            return await self._success_pick_student(session, ids[0], max(ids[1] if len(ids) > 1 else 0, 0))
        if payload.startswith("jr:ns:") and ids:
            return await self._success_start(session, user, ids[0])
        if payload.startswith("jr:cat:"):
            return await self._success_category(session, user, payload.removeprefix("jr:cat:"))
        if payload.startswith("jr:coins:") and ids:
            if user.state != ST_JR_COINS:
                return "Запись уже сохранена или отменена."
            if ids[0] not in (0, *successes_service.coin_choices(user)):
                return "Выберите сумму кнопкой."
            return await self._success_save(session, user, (user.state_data or {}), ids[0])
        return "Неизвестное действие."

    # --- просмотр ---

    async def _journal_hub(self, session: AsyncSession, user: User) -> Screen:
        classes = await classes_service.all_classes(session)
        total = await successes_service.total(session)
        lines = [
            "📒 Журнал успехов",
            "Учителя отмечают здесь успехи учеников: победы, старание, помощь, творчество. "
            "Записи копятся в карточке ученика — видно, в чём он силён.",
            "",
            f"Всего записей: {total}",
            "Выберите класс, чтобы посмотреть успехи учеников.",
        ]
        if not classes:
            lines[-1] = "Классов пока нет."
        return "\n".join(lines), kb.journal_hub(classes, successes_service.can_write(user))

    async def _class_pulse(self, session: AsyncSession, user: User, class_id: int) -> Screen | str:
        """Первый экран класса: как класс в целом и где учителю действовать."""
        pulse = await pulse_service.class_pulse(session, class_id)
        if pulse is None:
            return "Класс не найден."
        name = pulse.school_class.name
        if not pulse.students:
            return (
                f"📒 {name} · пульс класса\n\nВ классе пока нет учеников.",
                kb.class_pulse(class_id, 0, 0, successes_service.can_write(user)),
            )
        trend = ""
        if pulse.active_before:
            diff = len(pulse.active) - pulse.active_before
            arrow = "↑" if diff > 0 else "↓" if diff < 0 else "="
            trend = f" (на прошлой неделе {pulse.active_before} {arrow})"
        lines = [
            f"📒 {name} · пульс класса",
            f"Неделя с {pulse.week_start:%d.%m}",
            "",
            f"👥 Активны: {len(pulse.active)} из {len(pulse.students)}{trend}",
        ]
        if pulse.eligible and pulse.place_total:
            lines.append(
                f"🏁 Соревнование классов: {pulse.place_total}-е по сумме, "
                f"{pulse.place_average}-е в среднем на ученика"
            )
        elif pulse.eligible:
            lines.append("🏁 Соревнование классов: допуск есть, монет за неделю пока нет")
        else:
            lines.append(
                f"🏁 Соревнование классов: до допуска не хватает активных — {pulse.need_active}"
            )
        lines.append(
            f"🌟 Успехов за неделю: {pulse.successes_week} · 🏅 новых достижений: {pulse.new_achievements}"
        )
        if pulse.streaks:
            lines += ["", "🔥 Серии:"]
            lines += [f"• {s.display_name} — {days} дн., «{title}»" for s, title, days in pulse.streaks]
        lines.append("")
        lines.append(
            f"😴 Не участвуют 2+ недели: {len(pulse.inactive)}" if pulse.inactive
            else "😴 Неактивных нет — все в деле 👏"
        )
        lines.append(
            f"⚠️ Ответ отклонён и не пересдан: {len(pulse.need_help)}" if pulse.need_help
            else "⚠️ Отклонённых и не пересданных ответов нет"
        )
        return "\n".join(lines), kb.class_pulse(
            class_id, len(pulse.inactive), len(pulse.need_help), successes_service.can_write(user)
        )

    async def _pulse_list(self, session: AsyncSession, class_id: int, inactive: bool) -> Screen | str:
        pulse = await pulse_service.class_pulse(session, class_id)
        if pulse is None:
            return "Класс не найден."
        name = pulse.school_class.name
        if inactive:
            items = [
                (s, f"с {format_day(seen)}" if seen else "ещё ни разу") for s, seen in pulse.inactive
            ]
            title = (
                f"😴 {name}: не участвуют 2+ недели\n"
                "Последний отправленный ответ — рядом с именем. Возможно, ученику нужен "
                "интересный ему квест или личное приглашение."
            )
        else:
            items = [(s, f"«{quest}»") for s, quest in pulse.need_help]
            title = (
                f"⚠️ {name}: ответ отклонён и не пересдан\n"
                "Рядом с именем — квест. Подскажите ученику, что исправить: "
                "по кнопке откроется его карточка."
            )
        if not items:
            return f"{title}\n\nСейчас таких учеников нет 👏", kb.pulse_students([], class_id)
        return title, kb.pulse_students(items, class_id)

    async def _journal_students(self, session: AsyncSession, class_id: int, page: int) -> Screen | str:
        school_class = await session.get(SchoolClass, class_id)
        if school_class is None:
            return "Класс не найден."
        students = await classes_service.students(session, class_id)
        start = page * kb.JOURNAL_PAGE
        items = students[start : start + kb.JOURNAL_PAGE]
        ids = [s.id for s in students]
        counter = await successes_service.by_category(session, ids)
        lines = [f"📒 Класс «{school_class.name}»"]
        if students:
            total = sum(counter.values())
            lines.append(f"Успехов за всё время: {total}")
            if total:
                lines.append(successes_service.category_line(counter))
            lines.append("\n🌟 — записей об успехах, 🎯 — выполнено квестов.\nВыберите ученика:")
        else:
            lines.append("В классе пока нет учеников.")
        return "\n".join(lines), kb.journal_students(
            items,
            await successes_service.counts(session, [s.id for s in items]),
            await journal_service.quests_done(session, [s.id for s in items]),
            class_id,
            page,
            len(students) > start + kb.JOURNAL_PAGE,
        )

    async def _journal_card(self, session: AsyncSession, user: User, student_id: int, page: int) -> Screen | str:
        try:
            student = await successes_service.get_student(session, student_id)
        except SuccessError as exc:
            return str(exc)
        items, has_more = await successes_service.for_student(
            session, student.id, page * kb.SUCCESS_PAGE, kb.SUCCESS_PAGE
        )
        counter = await successes_service.by_category(session, [student.id])
        info = await journal_service.summary(session, student)
        lines = [f"📒 {student.display_name}"]
        if student.school_class:
            lines.append(f"Класс: {student.school_class.name}")
        total = sum(counter.values())
        lines += ["", f"🌟 Успехов: {total}"]
        if total:
            lines.append(successes_service.category_line(counter))
        lines += [
            f"🎯 Квестов выполнено: {info.quests_done} · 📈 заработано: {coins(info.earned)}",
            f"💰 Баланс: {coins(student.balance)}",
            "",
            "Записи об успехах — новые сверху, нажмите, чтобы прочитать:" if items
            else "Записей об успехах пока нет.",
        ]
        return "\n".join(lines), kb.journal_card(
            student, items, page, has_more, successes_service.can_write(user), format_day
        )

    async def _journal_feed(self, session: AsyncSession, page: int) -> Screen:
        items, has_more = await successes_service.recent(
            session, page * kb.SUCCESS_PAGE, kb.SUCCESS_PAGE
        )
        lines = ["🕘 Последние записи об успехах"]
        lines += [
            f"{format_day(s.created_at)} {successes_service.label(s).split(' ', 1)[0]} "
            f"{s.student.display_name}"
            f"{' (' + s.student.school_class.name + ')' if s.student.school_class else ''}"
            f" — {s.author.display_name}"
            for s in items
        ] or ["Записей пока нет."]
        return "\n".join(lines), kb.success_feed(items, page, has_more, format_day)

    async def _success_entry(self, session: AsyncSession, user: User, success_id: int) -> Screen | str:
        try:
            success = await successes_service.get(session, success_id)
        except SuccessError as exc:
            return str(exc)
        return (
            entry_text(success, with_student=True),
            kb.success_entry(success, successes_service.can_delete(user, success)),
        )

    async def _success_delete(self, session: AsyncSession, user: User, success_id: int) -> Screen | str:
        try:
            success = await successes_service.delete(session, user, success_id)
        except SuccessError as exc:
            return str(exc)
        if user.role in (Role.admin, Role.moderator):
            audit.record(user, "success.delete", f"№{success_id}, {success.student.display_name}")
        return await self._journal_card(session, user, success.student_id, 0)

    # --- новая запись: класс → ученик → категория → текст → монеты ---

    async def _success_pick_student(self, session: AsyncSession, class_id: int, page: int) -> Screen | str:
        school_class = await session.get(SchoolClass, class_id)
        if school_class is None:
            return "Класс не найден."
        students = await classes_service.students(session, class_id)
        if not students:
            return "В этом классе пока нет учеников."
        start = page * kb.JOURNAL_PAGE
        return (
            f"✍️ Отметить успех · {school_class.name}\nВыберите ученика:",
            kb.success_write_students(
                students[start : start + kb.JOURNAL_PAGE],
                class_id,
                page,
                len(students) > start + kb.JOURNAL_PAGE,
            ),
        )

    async def _success_start(self, session: AsyncSession, user: User, student_id: int) -> Screen | str:
        try:
            student = await successes_service.get_student(session, student_id)
        except SuccessError as exc:
            return str(exc)
        await users_service.set_state(session, user, ST_JR_CAT, {"student_id": student.id})
        return (
            f"✍️ Успех ученика: {student.display_name}\nВ какой области?",
            kb.success_categories(),
        )

    async def _success_category(self, session: AsyncSession, user: User, key: str) -> Screen | str:
        data = user.state_data or {}
        if user.state != ST_JR_CAT or "student_id" not in data:
            return "Запись уже сохранена или отменена."
        if key not in successes_service.BY_KEY:
            return "Неизвестная категория."
        await users_service.set_state(session, user, ST_JR_TEXT, {**data, "category": key})
        cat = successes_service.BY_KEY[key]
        return (
            f"{cat.emoji} {cat.title}\nОпишите успех одним сообщением: что сделал ученик. "
            "Ученик получит эту запись с вашим именем — пишите так, чтобы ему было приятно прочитать.",
            kb.cancel(),
        )

    async def _journal_step(self, session: AsyncSession, user: User, text: str) -> Screen:
        data = user.state_data or {}
        if user.state == ST_JR_CAT:
            return "Выберите область кнопкой ниже.", kb.success_categories()
        if user.state == ST_JR_COINS:
            return "Выберите кнопкой, начислить ли монеты.", kb.success_coins(
                successes_service.coin_choices(user)
            )
        if not text or text.startswith("/"):
            return "Опишите успех текстом одним сообщением или нажмите «Отмена».", kb.cancel()
        if len(text) > successes_service.TEXT_MAX:
            return f"Слишком длинно, максимум {successes_service.TEXT_MAX} символов.", kb.cancel()
        data = {**data, "text": text}
        try:
            student = await successes_service.get_student(session, data.get("student_id", 0))
        except SuccessError as exc:
            await users_service.set_state(session, user, None)
            return str(exc), None
        if successes_service.can_add_coins(user, student):
            await users_service.set_state(session, user, ST_JR_COINS, data)
            return (
                "Начислить ученику монеты вместе с записью?",
                kb.success_coins(successes_service.coin_choices(user)),
            )
        screen = await self._success_save(session, user, data, 0)
        return (screen, None) if isinstance(screen, str) else screen

    async def _success_save(self, session: AsyncSession, user: User, data: dict, amount: int) -> Screen | str:
        if "text" not in data:
            return "Запись уже сохранена или отменена."
        try:
            success = await successes_service.add(
                session, user, data.get("student_id", 0), data.get("category", ""), data["text"], amount
            )
        except SuccessError as exc:
            return str(exc)
        await users_service.set_state(session, user, None)
        if user.role == Role.admin:
            audit.record(user, "success.add", f"№{success.id}, {success.student.display_name}")
        student = success.student
        notice = f"🌟 {success.author.display_name} отметил(а) ваш успех!\n\n{successes_service.label(success)}\n{success.text}"
        if success.coins:
            notice += f"\n\n+{coins(success.coins)}. Баланс: {coins(student.balance)}"
        try:
            await self._send(student.max_user_id, notice, kb.success_notice())
        except httpx.HTTPError:
            pass
        extra = f" и начислено {coins(success.coins)}" if success.coins else ""
        hint = "" if success.coins or successes_service.can_add_coins(user, student) else (
            "\nМонеты к записи может добавить учитель класса ученика."
        )
        return (
            f"✅ Успех записан{extra}: {student.display_name}.\nУченик получил уведомление.{hint}",
            kb.success_saved(student.id),
        )

    # --- ученик ---

    async def _my_successes(self, session: AsyncSession, user: User, page: int) -> Screen | str:
        if user.role != Role.student:
            return "Это раздел ученика."
        items, has_more = await successes_service.for_student(
            session, user.id, page * kb.SUCCESS_PAGE, kb.SUCCESS_PAGE
        )
        if not items and page == 0:
            return (
                "🌟 Мои успехи\n\nПока записей нет. Когда учитель отметит ваш успех — "
                "победу, старание или помощь, — он появится здесь.",
                kb.my_successes(0, False),
            )
        counter = await successes_service.by_category(session, [user.id])
        lines = [f"🌟 Мои успехи: {sum(counter.values())}", successes_service.category_line(counter), ""]
        for s in items:
            lines += [entry_text(s), ""]
        return "\n".join(lines).strip(), kb.my_successes(page, has_more)
