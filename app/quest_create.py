"""Создание квеста (мастер по шагам), подтверждение квестов модераторов и «Мои квесты»."""
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app import keyboards as kb
from app.db.models import (
    Quest,
    QuestKind,
    QuestRepeat,
    Role,
    SchoolClass,
    SubmissionStatus,
    User,
)
from app.services import audit
from app.services import classes as classes_service
from app.services import quests as quests_service
from app.services import users as users_service
from app.services.quests import QuestError
from app.ui import Screen, find_image, parse_id

log = logging.getLogger(__name__)

# Шаги мастера создания квеста (User.state)
ST_NQ_TITLE = "nq_title"
ST_NQ_DESC = "nq_desc"
ST_NQ_REWARD = "nq_reward"
ST_NQ_KIND = "nq_kind"
ST_NQ_REPEAT = "nq_repeat"
ST_NQ_PHOTO = "nq_photo"  # последний шаг: картинка или «без картинки»
ST_NQ_AUDIENCE = "nq_audience"  # только у админа: для кого квест
ST_QUEST_REWORK = "quest_rework"  # админ пишет, что доработать в квесте модератора
NEW_QUEST_STATES = (
    ST_NQ_TITLE, ST_NQ_DESC, ST_NQ_REWARD, ST_NQ_KIND, ST_NQ_REPEAT, ST_NQ_PHOTO, ST_NQ_AUDIENCE
)

# Кнопки шагов мастера, которые не прерывают диалог
KEEP_STATE = ("nq:",)


class QuestCreateMixin:
    """Часть класса Bot: создание и управление квестами."""

    async def _new_quest_start(self, session: AsyncSession, user: User) -> Screen | str:
        if not quests_service.can_create(user):
            return "Создавать квесты могут учителя, модераторы и админы."
        if user.role == Role.teacher and not user.teacher_classes:
            return "Вам ещё не назначены классы. Обратитесь к администратору."
        await users_service.set_state(session, user, ST_NQ_TITLE, {})
        return (
            f"➕ Новый квест. Шаг 1 из {self._steps(user)}.\nНапишите название квеста.",
            kb.cancel(),
        )

    @staticmethod
    def _needs_audience(user: User) -> bool:
        # Админ, модератор и учитель нескольких классов выбирают, для кого квест
        return quests_service.is_staff(user) or len(user.teacher_classes) > 1

    @staticmethod
    def _steps(user: User) -> int:
        steps = 7 if QuestCreateMixin._needs_audience(user) else 6
        # Учитель и модератор не вводят награду и тип: квест с проверкой, награда по FIXED_RANGES
        return steps - 2 if quests_service.fixed_range(user) else steps

    async def _quest_audience_kb(self, session: AsyncSession, user: User) -> list[dict]:
        if quests_service.is_staff(user):
            return kb.quest_audience(await classes_service.all_classes(session))
        return kb.quest_audience(user.teacher_classes, allow_all=False)

    async def _new_quest_step(self, session: AsyncSession, user: User, text: str) -> Screen:
        data = dict(user.state_data or {})
        if user.state == ST_NQ_KIND:
            return "Выберите тип квеста кнопкой ниже.", kb.quest_kinds()
        if user.state == ST_NQ_REPEAT:
            return "Выберите кнопкой ниже, как часто можно выполнять квест.", kb.quest_repeats()
        if user.state == ST_NQ_AUDIENCE:
            return (
                "Выберите кнопкой ниже, для кого квест.",
                await self._quest_audience_kb(session, user),
            )
        if not text or text.startswith("/"):
            return "Пришлите ответ текстом или нажмите «Отмена».", kb.cancel()

        if user.state == ST_NQ_TITLE:
            if len(text) > quests_service.TITLE_MAX:
                return (
                    f"Слишком длинное название, максимум {quests_service.TITLE_MAX} символов.",
                    kb.cancel(),
                )
            data["title"] = text
            await users_service.set_state(session, user, ST_NQ_DESC, data)
            return (
                f"Шаг 2 из {self._steps(user)}.\nОпишите, что нужно сделать ученику.",
                kb.cancel(),
            )

        if user.state == ST_NQ_DESC:
            if len(text) > quests_service.DESCRIPTION_MAX:
                return (
                    f"Слишком длинное описание, максимум {quests_service.DESCRIPTION_MAX} символов.",
                    kb.cancel(),
                )
            data["description"] = text
            fixed = quests_service.fixed_range(user)
            if fixed:
                data["reward_min"], data["reward"] = fixed
                data["kind"] = QuestKind.photo.value
                return await self._new_quest_repeat_prompt(
                    session,
                    user,
                    data,
                    f"Награда: {fixed[0]}–{fixed[1]} монет, точную сумму выберет проверяющий.\n"
                    "Ученик присылает ответ на проверку: текст, фото или видео.\n\n",
                )
            await users_service.set_state(session, user, ST_NQ_REWARD, data)
            limit = quests_service.reward_bounds(user)[1]
            return (
                f"Шаг 3 из {self._steps(user)}.\nНаграда в монетах: число от 1 до {limit}.\n"
                "Для квеста с проверкой можно диапазон, например 100-500: "
                "точную сумму выберет проверяющий.",
                kb.cancel(),
            )

        # ST_NQ_REWARD
        try:
            data["reward_min"], data["reward"] = quests_service.parse_reward_range(user, text)
        except QuestError as exc:
            return str(exc), kb.cancel()
        await users_service.set_state(session, user, ST_NQ_KIND, data)
        return (
            f"Шаг 4 из {self._steps(user)}.\nКак подтверждать выполнение?\n"
            "✅ Автоподтверждение: ученик нажимает «Выполнено» и сразу получает монеты.\n"
            "📝 Ответ на проверку: ученик присылает текст, фото или видео, вы подтверждаете.",
            kb.quest_kinds(),
        )

    async def _new_quest_kind(
        self, session: AsyncSession, user: User, kind_value: str
    ) -> Screen | str:
        if user.state != ST_NQ_KIND:
            return "Создание квеста уже завершено или отменено."
        try:
            kind = QuestKind(kind_value)
        except ValueError:
            return "Неизвестный тип квеста."
        if kind != QuestKind.photo and (user.state_data or {}).get("reward_min") is not None:
            return "Диапазон награды — только для квеста с проверкой: выберите «📝 Ответ на проверку»."
        data = dict(user.state_data or {}, kind=kind.value)
        return await self._new_quest_repeat_prompt(session, user, data)

    async def _new_quest_repeat_prompt(
        self, session: AsyncSession, user: User, data: dict, intro: str = ""
    ) -> Screen:
        await users_service.set_state(session, user, ST_NQ_REPEAT, data)
        step = 3 if quests_service.fixed_range(user) else 5
        return (
            f"{intro}Шаг {step} из {self._steps(user)}.\nКак часто ученик может выполнять квест?\n"
            "🚫 Не повторять: один раз (обычно так).\n"
            "🔁 Раз в день, неделю или месяц: квест снова появится у ученика "
            "с полуночи, с понедельника или с 1-го числа (по Москве).",
            kb.quest_repeats(),
        )

    async def _new_quest_repeat(
        self, session: AsyncSession, user: User, repeat_value: str
    ) -> Screen | str:
        if user.state != ST_NQ_REPEAT:
            return "Создание квеста уже завершено или отменено."
        try:
            repeat = QuestRepeat(repeat_value)
        except ValueError:
            return "Неизвестная периодичность."
        data = dict(user.state_data or {}, repeat=repeat.value)
        if not self._needs_audience(user):
            # У учителя один класс — квест для него
            data["class_id"] = user.teacher_classes[0].id
            return await self._new_quest_photo_prompt(session, user, data)
        await users_service.set_state(session, user, ST_NQ_AUDIENCE, data)
        steps = self._steps(user)
        return (
            f"Шаг {steps - 1} из {steps}.\nДля кого квест?",
            await self._quest_audience_kb(session, user),
        )

    async def _new_quest_photo_prompt(
        self, session: AsyncSession, user: User, data: dict
    ) -> Screen:
        await users_service.set_state(session, user, ST_NQ_PHOTO, data)
        steps = self._steps(user)
        return (
            f"Шаг {steps} из {steps}.\n🖼 Пришлите картинку к квесту (как фото, не файлом) — "
            "ученики увидят её в карточке квеста и в уведомлении.\n"
            "Картинку можно добавить и позже: «✏️ Изменить» в карточке квеста.",
            kb.quest_photo_choice(),
        )

    async def _new_quest_photo(
        self, session: AsyncSession, user: User, attachments: list[dict], skip: bool = False
    ) -> Screen | str:
        if user.state != ST_NQ_PHOTO:
            return "Создание квеста уже завершено или отменено."
        photo = None if skip else find_image(attachments)
        if photo is None and not skip:
            return (
                "Пришлите картинку как фото (не файлом) или нажмите «Без картинки».",
                kb.quest_photo_choice(),
            )
        return await self._new_quest_finish(
            session, user, (user.state_data or {}).get("class_id"), photo
        )

    async def _new_quest_audience(
        self, session: AsyncSession, user: User, value: str
    ) -> Screen | str:
        if user.state != ST_NQ_AUDIENCE:
            return "Создание квеста уже завершено или отменено."
        if value == "all":
            class_id = None
        else:
            try:
                class_id = int(value)
            except ValueError:
                return "Класс не найден."
            # Проверяем сразу, а не после картинки
            if await session.get(SchoolClass, class_id) is None:
                return "Класс не найден."
            if not quests_service.is_staff(user) and class_id not in user.teacher_class_ids:
                return "Можно выбрать только свой класс."
        data = dict(user.state_data or {}, class_id=class_id)
        return await self._new_quest_photo_prompt(session, user, data)

    async def _new_quest_finish(
        self, session: AsyncSession, user: User, class_id: int | None, photo: dict | None = None
    ) -> Screen | str:
        data = user.state_data or {}
        try:
            kind = QuestKind(data.get("kind", QuestKind.auto.value))
            repeat = QuestRepeat(data.get("repeat", QuestRepeat.none.value))
            quest = await quests_service.create_quest(
                session,
                user,
                data["title"],
                data.get("description", ""),
                data["reward"],
                kind,
                class_id,
                photo,
                repeat=repeat,
                reward_min=data.get("reward_min"),
            )
        except (QuestError, KeyError, ValueError) as exc:
            await users_service.set_state(session, user, None)
            return str(exc) if isinstance(exc, QuestError) else "Не удалось создать квест."
        await users_service.set_state(session, user, None)
        school_class = (
            await session.get(SchoolClass, quest.class_id) if quest.class_id else None
        )
        audience = f"ученикам класса «{school_class.name}»" if school_class else "всей школе"
        if user.role in (Role.moderator, Role.admin):
            audit.record(
                user, "quest.create",
                f"№{quest.id} «{quest.title}», {kb.reward_text(quest)}, {audience}",
            )
        summary = (
            f"«{quest.title}»\nНаграда: {kb.reward_text(quest)}\n"
            f"Тип: {kb.KIND_TITLES[quest.kind]}\n"
            f"Повтор: {kb.REPEAT_TITLES[quest.repeat].lower()}"
        )
        if quest.approval == quests_service.APPROVAL_PENDING:
            await self._ask_quest_approval(session, user, quest, audience)
            return (
                f"📨 Квест создан и отправлен админу на подтверждение.\n"
                f"После публикации он станет доступен {audience}, а ученики получат уведомление. "
                f"Решение админа придёт вам сообщением.\n\n{summary}",
                self._quest_image(quest) + kb.back_to_menu(),
            )
        sent = await self._announce_quest(session, quest)
        return (
            f"🎉 Квест создан и доступен {audience}.\n"
            f"Уведомление получат учеников: {sent}.\n\n{summary}",
            self._quest_image(quest) + kb.back_to_menu(),
        )

    async def _quest_audience(self, session: AsyncSession, quest) -> str:
        school_class = (
            await session.get(SchoolClass, quest.class_id) if quest.class_id else None
        )
        return f"ученикам класса «{school_class.name}»" if school_class else "всей школе"

    async def _announce_quest(self, session: AsyncSession, quest) -> int:
        """Разослать ученикам уведомление о новом квесте (в фоне); сколько получателей."""
        recipients = [s.max_user_id for s in await quests_service.students_for(session, quest)]
        notice = (
            f"🆕 Новый квест: «{quest.title}»\nНаграда: {kb.reward_text(quest)}\n"
            f"{self._repeat_line(quest)}{self._author_line(quest)}"
        )
        await self._run_later(
            self._broadcast(
                recipients, notice, self._quest_image(quest) + kb.new_quest_notice(quest.id)
            )
        )
        return len(recipients)

    async def _ask_quest_approval(
        self, session: AsyncSession, author: User, quest, audience: str, again: bool = False
    ) -> None:
        """Админам: квест модератора ждёт публикации."""
        head = (
            f"📨 Модератор {author.display_name} доработал(а) квест и снова просит опубликовать его"
            if again
            else f"📨 Модератор {author.display_name} создал(а) квест и просит опубликовать его"
        )
        text = (
            f"{head} ({audience}):\n\n🎯 {quest.title}\n"
            + (f"\n{quest.description}\n" if quest.description else "")
            + f"\nНаграда: {kb.reward_text(quest)}\nТип: {kb.KIND_TITLES[quest.kind]}\n"
            f"Повтор: {kb.REPEAT_TITLES[quest.repeat].lower()}"
        )
        await self._notify(
            await users_service.admins(session),
            text[:3900],
            self._quest_image(quest) + kb.quest_approval(quest.id),
        )

    async def _quest_approve(self, session: AsyncSession, user: User, quest_id: int) -> Screen | str:
        try:
            quest = await quests_service.approve_quest(session, user, quest_id)
        except QuestError as exc:
            return str(exc)
        audience = await self._quest_audience(session, quest)
        audit.record(user, "quest.approve", f"№{quest.id} «{quest.title}», {audience}")
        sent = await self._announce_quest(session, quest)
        await self._notify(
            [quest.author],
            f"✅ Админ опубликовал(а) ваш квест «{quest.title}». "
            f"Теперь он доступен {audience}, уведомление получат учеников: {sent}.",
            kb.new_quest_notice_admin(quest.id),
        )
        return await self._managed_card(
            session, user, quest, f"✅ Квест опубликован. Уведомление получат учеников: {sent}."
        )

    async def _quest_rework_prompt(self, session: AsyncSession, user: User, quest_id: int) -> Screen | str:
        if user.role != Role.admin:
            return "Публикацию квеста подтверждает админ."
        quest = await session.get(Quest, quest_id)
        if quest is None or quest.approval != quests_service.APPROVAL_PENDING:
            return "Квест уже рассмотрен."
        await users_service.set_state(session, user, ST_QUEST_REWORK, {"quest_id": quest.id})
        return (
            f"↩️ Вернуть квест «{quest.title}» на доработку?\n\n"
            "Напишите одним сообщением, что нужно исправить — это увидит модератор "
            f"(до {quests_service.REASON_MAX} символов). Или верните без комментария.",
            kb.quest_rework(quest.id),
        )

    async def _quest_return(
        self, session: AsyncSession, user: User, quest_id: int, note: str | None = None
    ) -> Screen | str:
        await users_service.set_state(session, user, None)
        try:
            quest = await quests_service.return_quest(session, user, quest_id, note)
        except QuestError as exc:
            return str(exc)
        audit.record(
            user, "quest.rework",
            f"№{quest.id} «{quest.title}»" + (f": {quest.approval_note}" if quest.approval_note else ""),
        )
        await self._notify(
            [quest.author],
            f"↩️ Админ вернул(а) ваш квест «{quest.title}» на доработку."
            + (f"\nКомментарий: {quest.approval_note}" if quest.approval_note else "")
            + "\n\nИсправьте квест и нажмите «📨 Отправить на подтверждение».",
            kb.new_quest_notice_admin(quest.id),
        )
        return await self._managed_card(session, user, quest, "↩️ Квест возвращён на доработку.")

    async def _quest_resubmit(self, session: AsyncSession, user: User, quest_id: int) -> Screen | str:
        try:
            quest = await quests_service.resubmit_quest(session, user, quest_id)
        except QuestError as exc:
            return str(exc)
        audience = await self._quest_audience(session, quest)
        audit.record(user, "quest.resubmit", f"№{quest.id} «{quest.title}»")
        await self._ask_quest_approval(session, user, quest, audience, again=True)
        return await self._managed_card(session, user, quest, "📨 Квест снова отправлен админу.")

    async def _manage(self, session: AsyncSession, user: User, payload: str) -> Screen | str:
        if user.role not in (Role.teacher, Role.admin, Role.moderator):
            return "Раздел доступен учителям, модераторам и админам."
        _, action, *rest = payload.split(":")
        if action == "list":
            active = rest[0] == "a" if rest else True
            return await self._managed_list(session, user, active, parse_id(payload) or 0)
        quest_id = parse_id(payload) or 0
        try:
            if action == "open":
                quest = await quests_service.get_viewable(session, user, quest_id)
                return await self._managed_card(session, user, quest)
            if action == "close":
                quest = await quests_service.get_managed(session, user, quest_id)
                if not quest.is_active:
                    return "Квест уже закрыт."
                return (
                    f"Закрыть квест «{quest.title}»?\n\n"
                    "Ученики перестанут его видеть. Заявки, которые уже ждут проверки, "
                    "можно будет рассмотреть.",
                    kb.confirm_close(quest.id),
                )
            if action in ("edit", "ef", "rep", "photo_rm", "rv"):
                return await self._quest_edit(session, user, action, rest, quest_id)
            if action == "apr":
                return await self._quest_approve(session, user, quest_id)
            if action == "rwk":
                return await self._quest_rework_prompt(session, user, quest_id)
            if action == "rwk0":
                return await self._quest_return(session, user, quest_id)
            if action == "resub":
                return await self._quest_resubmit(session, user, quest_id)
            if action in ("close_ok", "reopen"):
                quest = await quests_service.set_active(
                    session, user, quest_id, active=action == "reopen"
                )
                note = "🟢 Квест снова открыт." if quest.is_active else "🔒 Квест закрыт."
                if user.role in (Role.moderator, Role.admin):
                    audit.record(
                        user, "quest.reopen" if quest.is_active else "quest.close",
                        f"№{quest.id} «{quest.title}»",
                    )
                return await self._managed_card(session, user, quest, note)
        except QuestError as exc:
            return str(exc)
        return "Неизвестное действие."

    async def _managed_list(
        self, session: AsyncSession, user: User, active: bool, page: int
    ) -> Screen:
        page = max(page, 0)
        items, has_more = await quests_service.managed_quests(
            session, user, active, page * kb.MANAGE_PAGE, kb.MANAGE_PAGE
        )
        is_admin = user.role == Role.admin
        whose = {Role.admin: "Все квесты", Role.moderator: "Квесты школы"}.get(
            user.role, "Мои квесты"
        )
        title = f"📂 {whose}: {'активные' if active else 'закрытые'}"
        if page:
            title += f", страница {page + 1}"
        viewer = None if is_admin else user.id
        if not items:
            empty = "Активных квестов нет." if active else "Закрытых квестов нет."
            return f"{title}\n\n{empty}", kb.managed_list([], active, 0, False, viewer)
        if user.role == Role.moderator:
            title += (
                "\n🏫 — администрации, 👥 — учителей: только просмотр; "
                "свои вы можете менять и закрывать"
            )
        elif not is_admin:
            title += (
                "\nЗдесь все квесты, которые видят ваши ученики. "
                "🏫 — администрации, 👥 — коллег: только просмотр, "
                "ответы своих учеников вы проверяете"
            )
        return (
            f"{title}\nВыберите квест:",
            kb.managed_list(items, active, page, has_more, viewer),
        )

    async def _managed_card(
        self, session: AsyncSession, user: User, quest, note: str = ""
    ) -> Screen:
        stats = await quests_service.quest_stats(session, quest.id)
        school_class = (
            await session.get(SchoolClass, quest.class_id) if quest.class_id else None
        )
        lines = [note, ""] if note else []
        lines.append(f"🎯 {quest.title}")
        if quest.description:
            lines += ["", quest.description]
        lines += [
            "",
            f"Награда: {kb.reward_text(quest)}",
            f"Тип: {kb.KIND_TITLES[quest.kind]}",
            f"Повтор: {kb.REPEAT_TITLES[quest.repeat].lower()}",
            f"Для кого: {f'класс «{school_class.name}»' if school_class else 'вся школа'}",
        ]
        reviewers = await quests_service.reviewer_names(session, quest)
        if reviewers:
            lines.append(f"Проверяют: {reviewers} (и админ)")
        if quest.author_id != user.id:
            lines.append(self._author_line(quest))
        if quest.approval == quests_service.APPROVAL_PENDING:
            lines.append("Статус: ⏳ ждёт подтверждения админа, ученики его пока не видят")
        elif quest.approval == quests_service.APPROVAL_REWORK:
            lines.append("Статус: ✏️ на доработке, ученики его пока не видят")
            if quest.approval_note:
                lines.append(f"Комментарий админа: {quest.approval_note}")
        else:
            lines.append(f"Статус: {'🟢 активен' if quest.is_active else '🔒 закрыт'}")
        done_title = "Выполнили" if quest.repeat == QuestRepeat.none else "Выполнений всего"
        lines.append(f"{done_title}: {stats.get(SubmissionStatus.approved, 0)}")
        can_manage = quests_service.can_manage(user, quest)
        if quest.kind == QuestKind.photo:
            lines.append(f"Ждут проверки: {stats.get(SubmissionStatus.pending, 0)}")
        if not can_manage and user.role == Role.moderator:
            lines += ["", "Это не ваш квест: только просмотр."]
        elif not can_manage:
            whose = (
                "администрации"
                if quests_service.is_admin_quest(quest)
                else f"коллеги ({quest.author.display_name})"
            )
            lines += [
                "",
                f"Это квест {whose}: изменить и закрыть его может только автор, "
                "а ответы ваших учеников вы проверяете в разделе «На проверке».",
            ]
        return (
            "\n".join(lines),
            self._quest_image(quest)
            + kb.managed_card(
                quest, can_manage, show_locked=user.role == Role.moderator,
                is_admin=user.role == Role.admin,
            ),
        )

    # --- маршрут раздела и текст шагов ---

    async def _new_quest_callback(self, session: AsyncSession, user: User, payload: str) -> Screen | str:
        if payload == "menu:new_quest":
            return await self._new_quest_start(session, user)
        if payload.startswith("nq:kind:"):
            return await self._new_quest_kind(session, user, payload.rsplit(":", 1)[1])
        if payload == "nq:photo:none":
            return await self._new_quest_photo(session, user, [], skip=True)
        if payload.startswith("nq:rep:"):
            return await self._new_quest_repeat(session, user, payload.rsplit(":", 1)[1])
        if payload.startswith("nq:aud:"):
            return await self._new_quest_audience(session, user, payload.rsplit(":", 1)[1])
        return "Неизвестное действие."

    async def _new_quest_text(
        self, session: AsyncSession, user: User, text: str, attachments: list[dict]
    ) -> Screen:
        if user.state == ST_NQ_PHOTO:
            return await self._new_quest_photo(session, user, attachments)
        return await self._new_quest_step(session, user, text)

    async def _quest_rework_text(self, session: AsyncSession, user: User, text: str) -> Screen:
        """Админ пишет, что доработать в квесте модератора."""
        if not text or text.startswith("/"):
            return "Напишите комментарий текстом или нажмите «Вернуть без комментария».", None
        quest_id = (user.state_data or {}).get("quest_id", 0)
        result = await self._quest_return(session, user, quest_id, text)
        return (result, None) if isinstance(result, str) else result
