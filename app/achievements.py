"""Раздел «🏅 Достижения»: витрина ученика и каталог админа (создание, выдача, бонусы)."""
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app import keyboards as kb
from app.db.models import AchievementKind, Role, SchoolClass, User
from app.keyboards import coins
from app.services import achievements as ach_service
from app.services import classes as classes_service
from app.services import profile as profile_service
from app.services import users as users_service
from app.services.achievements import AchievementError
from app.ui import Screen, format_day, parse_id, parse_ids
from app.ui import days_word as _days

log = logging.getLogger(__name__)

ST_ACH_TITLE = "ach_title"
ST_ACH_DESC = "ach_desc"
ST_ACH_EMOJI = "ach_emoji"
ST_ACH_REWARD = "ach_reward"
ST_ACH_SET_REWARD = "ach_set_reward"
ACH_STATES = (ST_ACH_TITLE, ST_ACH_DESC, ST_ACH_EMOJI, ST_ACH_REWARD, ST_ACH_SET_REWARD)
KEEP_STATE = ("ach:emoji:",)  # выбор значка — шаг создания достижения

KIND_TITLES = {
    AchievementKind.manual: "выдаёт админ",
    AchievementKind.streak: "авто: серия ежедневного квеста",
    AchievementKind.quests_count: "авто: число выполненных квестов",
    AchievementKind.coins_earned: "авто: заработанные монеты",
    AchievementKind.first_purchase: "авто: первая покупка",
}
# Ограничение длины сообщения MAX — 4000 символов
TEXT_LIMIT = 3800


class AchievementsMixin:
    """Часть класса Bot: достижения."""

    # --- автодостижения: вызывается после событий, меняющих прогресс ученика ---

    async def _check_achievements(self, session: AsyncSession, student: User) -> None:
        if not self.auto_achievements:
            return
        for a in await ach_service.check_auto(session, student):
            await self._notify([student], self._achievement_notice(a), kb.to_achievements())

    @staticmethod
    def _achievement_notice(a) -> str:
        text = f"🏅 Новое достижение!\n\n{a.emoji} {a.title}"
        if a.description:
            text += f"\n{a.description}"
        if a.reward:
            text += f"\n\n+{coins(a.reward)}"
        return text

    async def _achievements(self, session: AsyncSession, user: User, payload: str) -> Screen | str:
        if payload == "menu:achievements":
            if user.role in (Role.admin, Role.moderator):
                return await self._ach_catalog(session, readonly=user.role != Role.admin)
            if user.role == Role.student:
                return await self._ach_showcase(session, user)
            return "Раздел пока доступен ученикам и админам."
        if user.role == Role.moderator and payload in ("ach:list",) or (
            user.role == Role.moderator and payload.startswith("ach:open:")
        ):
            try:
                if payload == "ach:list":
                    return await self._ach_catalog(session, readonly=True)
                return await self._ach_card(session, parse_id(payload) or 0, readonly=True)
            except AchievementError as exc:
                return str(exc)
        if user.role != Role.admin:
            return "Это действие доступно только админу."
        try:
            return await self._ach_admin(session, user, payload)
        except AchievementError as exc:
            return str(exc)

    # --- ученик ---

    async def _profile(self, session: AsyncSession, user: User) -> Screen | str:
        if user.role != Role.student:
            return "Профиль есть только у учеников."
        p = await profile_service.build(session, user)
        lines = [f"👤 {user.display_name}"]
        if user.school_class:
            lines.append(f"Класс: {user.school_class.name}")
        lines += ["", f"💰 Баланс: {coins(user.balance)}"]
        if p.school_place:
            lines.append(f"🏆 Место в школе: {p.school_place} из {p.school_total}")
        if p.class_place:
            lines.append(f"👥 Место в классе: {p.class_place} из {p.class_total}")
        lines += [
            "",
            f"🎯 Квестов выполнено: {p.quests_done}",
            f"📈 Заработано всего: {coins(p.earned)}",
        ]
        if p.pending:
            lines.append(f"⏳ Ждут проверки: {p.pending}")
        if p.streaks:
            lines += ["", "🔥 Серии:"]
            for s in p.streaks:
                today = "сегодня ✅" if s.done_today else "сегодня ещё не выполнено"
                lines.append(f"• {s.title} — {s.days} {_days(s.days)} подряд ({today})")
        lines += ["", f"🏅 Достижения: {p.achievements} из {p.achievements_total}"]
        for a, granted_at in p.recent:
            lines.append(f"{a.emoji} {a.title} · {format_day(granted_at)}")
        lines.append(
            "\n🔕 Утренние напоминания выключены"
            if user.reminders_off
            else "\n🔔 Утренние напоминания о сериях включены"
        )
        return "\n".join(lines), kb.profile_actions(user.reminders_off)

    async def _reminders_toggle(self, session: AsyncSession, user: User, off: bool) -> Screen | str:
        if user.role != Role.student:
            return "Напоминания приходят только ученикам."
        user.reminders_off = off
        await session.commit()
        if off:
            return (
                "🔕 Утренние напоминания выключены. Серии и достижения работают как раньше.\n"
                "Включить снова можно в «👤 Профиле».",
                kb.back_to_menu(),
            )
        return "🔔 Утренние напоминания снова включены.", kb.back_to_menu()

    async def _ach_showcase(self, session: AsyncSession, student: User) -> Screen:
        show = await ach_service.showcase(session, student)
        total = len(show.earned) + len(show.locked)
        lines = [f"🏅 Мои достижения: {len(show.earned)} из {total}"]
        if show.earned:
            lines.append("")
            for a, granted_at in show.earned:
                lines.append(f"{a.emoji} {a.title} · {format_day(granted_at)}")
                if a.description:
                    lines.append(f"     {a.description}")
        else:
            lines += ["", "Пока нет ни одного — выполняйте квесты, и первое не заставит ждать!"]
        if show.locked:
            lines += ["", "🔒 Ещё не получены:"]
            for a, value, threshold in show.locked:
                line = f"🔒 {a.emoji} {a.title} — {a.description.lower() if a.description else ''}"
                if threshold > 1:
                    line += f" ({value} из {threshold})"
                lines.append(line)
        text = "\n".join(lines)
        if len(text) > TEXT_LIMIT:
            text = text[:TEXT_LIMIT].rsplit("\n", 1)[0] + "\n…"
        return text, kb.back_to_menu()

    # --- админ ---

    async def _ach_catalog(
        self, session: AsyncSession, note: str = "", readonly: bool = False
    ) -> Screen:
        items = await ach_service.all_achievements(session)
        holders = await ach_service.holders_count(session)
        lines = [note, ""] if note else []
        lines += [
            "🏅 Достижения",
            "Число после названия — сколько учеников уже получили.",
            "Автодостижения выдаются сами; серии появляются для каждого ежедневного квеста.",
        ]
        return "\n".join(lines), kb.achievements_catalog(items, holders, readonly)

    async def _ach_card(
        self, session: AsyncSession, achievement_id: int, note: str = "", readonly: bool = False
    ) -> Screen:
        a = await ach_service.get(session, achievement_id)
        holders = (await ach_service.holders_count(session)).get(a.id, 0)
        lines = [note, ""] if note else []
        lines.append(f"{a.emoji} {a.title}")
        if a.description:
            lines.append(a.description)
        lines += [
            "",
            f"Вид: {KIND_TITLES[a.kind]}",
            f"Бонус: {coins(a.reward)}",
            f"Получили: {holders}",
        ]
        return "\n".join(lines), kb.achievement_card(a, readonly)

    async def _ach_admin(self, session: AsyncSession, user: User, payload: str) -> Screen | str:
        if payload == "ach:list":
            return await self._ach_catalog(session)
        if payload == "ach:new":
            await users_service.set_state(session, user, ST_ACH_TITLE, {})
            return "➕ Новое достижение. Шаг 1 из 4.\nНапишите название.", kb.cancel()
        if payload == "ach:emoji:default":
            if user.state != ST_ACH_EMOJI:
                return "Создание достижения уже завершено или отменено."
            return await self._ach_step(session, user, "", default_emoji=True)
        if payload.startswith("ach:open:"):
            return await self._ach_card(session, parse_id(payload) or 0)
        if payload.startswith("ach:reward:"):
            a = await ach_service.get(session, parse_id(payload) or 0)
            await users_service.set_state(session, user, ST_ACH_SET_REWARD, {"id": a.id})
            return (
                f"💰 Новый бонус за «{a.title}» (сейчас {coins(a.reward)}).\n"
                f"Число от 0 до {ach_service.REWARD_MAX}. Уже выданный бонус не меняется.",
                kb.cancel(),
            )
        if payload.startswith("ach:gcls:"):
            a = await ach_service.get(session, parse_id(payload) or 0)
            classes = await classes_service.all_classes(session)
            return f"🎖 Кому выдать «{a.title}»? Выберите класс:", kb.achievement_classes(a.id, classes)
        if payload.startswith("ach:gst:"):
            # ach:gst:<достижение>:<класс>:<страница>
            ids = parse_ids(payload, 3)
            if ids is None:
                return "Неизвестное действие."
            return await self._ach_students(session, ids[0], ids[1], max(ids[2], 0))
        if payload.startswith(("ach:give:", "ach:rev:", "ach:rev_ok:")):
            ids = parse_ids(payload, 2)
            if ids is None:
                return "Неизвестное действие."
            return await self._ach_give(session, user, payload, ids[0], ids[1])
        return "Неизвестное действие."

    async def _ach_students(
        self, session: AsyncSession, achievement_id: int, class_id: int, page: int, note: str = ""
    ) -> Screen | str:
        a = await ach_service.get(session, achievement_id)
        school_class = await session.get(SchoolClass, class_id)
        if school_class is None:
            return "Класс не найден."
        students = await classes_service.students(session, class_id)
        start = page * kb.ACH_STUDENTS_PAGE
        items = students[start : start + kb.ACH_STUDENTS_PAGE]
        owners = {s.id for s in items if await ach_service.has(session, a.id, s)}
        lines = [note, ""] if note else []
        lines.append(f"🎖 «{a.title}» · класс «{school_class.name}»")
        if students:
            lines.append("Нажмите на ученика, чтобы выдать. ✅ — уже получил, нажатие предложит отозвать.")
        else:
            lines.append("В классе пока нет учеников.")
        return "\n".join(lines), kb.achievement_students(
            a.id, class_id, items, owners, page, len(students) > start + kb.ACH_STUDENTS_PAGE
        )

    async def _ach_give(
        self, session: AsyncSession, admin: User, payload: str, achievement_id: int, student_id: int
    ) -> Screen | str:
        a = await ach_service.get(session, achievement_id)
        if a.kind != AchievementKind.manual:
            return "Автодостижения выдаются сами."
        student = await session.get(User, student_id)
        if student is None or student.role != Role.student or student.full_name is None:
            return "Ученик не найден."
        class_id = student.class_id or 0
        if payload.startswith("ach:give:"):
            if await ach_service.has(session, a.id, student):
                return (
                    f"У {student.display_name} уже есть «{a.title}». Отозвать?\n"
                    f"Бонус {coins(a.reward)} спишется (но баланс не уйдёт в минус).",
                    kb.achievement_revoke_confirm(a.id, student.id, class_id),
                )
            await ach_service.grant(session, a, student, admin)
            await self._notify([student], self._achievement_notice(a), kb.to_achievements())
            await self._check_achievements(session, student)  # бонус мог открыть «монетное»
            return await self._ach_students(
                session, a.id, class_id, 0, f"✅ {student.display_name} получил(а) «{a.title}»."
            )
        if payload.startswith("ach:rev_ok:"):
            taken = await ach_service.revoke(session, a, student, admin)
            note = f"↩️ У {student.display_name} отозвано «{a.title}»"
            note += f", списано {coins(taken)}." if taken else "."
            return await self._ach_students(session, a.id, class_id, 0, note)
        return "Неизвестное действие."

    async def _ach_step(
        self, session: AsyncSession, user: User, text: str, default_emoji: bool = False
    ) -> Screen:
        data = dict(user.state_data or {})
        if not default_emoji and (not text or text.startswith("/")):
            return "Пришлите ответ текстом или нажмите «Отмена».", kb.cancel()
        try:
            if user.state == ST_ACH_SET_REWARD:
                reward = ach_service.parse_reward(text)
                a = await ach_service.set_reward(session, data.get("id", 0), reward)
                await users_service.set_state(session, user, None)
                return await self._ach_card(session, a.id, f"💰 Бонус теперь {coins(reward)}.")
            if user.state == ST_ACH_TITLE:
                if len(text) > ach_service.TITLE_MAX:
                    return f"Слишком длинно, максимум {ach_service.TITLE_MAX} символов.", kb.cancel()
                data["title"] = text
                await users_service.set_state(session, user, ST_ACH_DESC, data)
                return "Шаг 2 из 4.\nКоротко: за что даётся достижение?", kb.cancel()
            if user.state == ST_ACH_DESC:
                if len(text) > ach_service.DESCRIPTION_MAX:
                    return f"Слишком длинно, максимум {ach_service.DESCRIPTION_MAX} символов.", kb.cancel()
                data["description"] = text
                await users_service.set_state(session, user, ST_ACH_EMOJI, data)
                return (
                    "Шаг 3 из 4.\nПришлите один эмодзи-значок, например 🎨 или 🧪.",
                    kb.achievement_emoji_choice(),
                )
            if user.state == ST_ACH_EMOJI:
                emoji = ach_service.DEFAULT_EMOJI if default_emoji else text.strip()
                if len(emoji) > ach_service.EMOJI_MAX:
                    return "Пришлите только эмодзи, без текста.", kb.achievement_emoji_choice()
                data["emoji"] = emoji
                await users_service.set_state(session, user, ST_ACH_REWARD, data)
                return (
                    f"Шаг 4 из 4.\nБонус в монетах при получении: число от 0 до {ach_service.REWARD_MAX}.",
                    kb.cancel(),
                )
            # ST_ACH_REWARD
            reward = ach_service.parse_reward(text)
            a = await ach_service.create_manual(
                session, user, data["title"], data.get("description", ""), data["emoji"], reward
            )
            await users_service.set_state(session, user, None)
            return await self._ach_card(session, a.id, "🎉 Достижение создано. Теперь его можно выдать.")
        except AchievementError as exc:
            return str(exc), kb.cancel()
        except KeyError:
            await users_service.set_state(session, user, None)
            return "Не удалось создать достижение, начните заново.", kb.back_to_menu()
