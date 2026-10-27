"""Обработка событий MAX. Хендлеры только принимают события и вызывают сервисы.

Хендлеры возвращают «экран» (текст + клавиатура). На текстовое сообщение экран
приходит новым сообщением, на нажатие кнопки им заменяется текущее сообщение.
"""
import asyncio
import logging
from collections.abc import Coroutine

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import demo as demo_service
from app import health, help_texts
from app import keyboards as kb
from app.achievements import ACH_STATES, AchievementsMixin
from app.achievements import KEEP_STATE as ACH_KEEP
from app.broadcasts_view import BROADCAST_STATES, BroadcastsMixin
from app.broadcasts_view import KEEP_STATE as BROADCAST_KEEP
from app.channel import ST_PUB_FIX, ST_PUB_NOTE, ChannelMixin
from app.coins_view import COINS_STATES, CoinsMixin
from app.coins_view import KEEP_STATE as COINS_KEEP
from app.contests import ContestsMixin
from app.db.models import Role, User
from app.demo_view import DemoMixin
from app.journal import JOURNAL_STATES, JournalMixin
from app.journal import KEEP_STATE as JOURNAL_KEEP
from app.keyboards import MENUS, ROLE_TITLES, coins
from app.max_api import MaxClient
from app.quest_create import KEEP_STATE as NEW_QUEST_KEEP
from app.quest_create import NEW_QUEST_STATES, ST_QUEST_REWORK, QuestCreateMixin
from app.quest_edit import QUEST_EDIT_STATES, QuestEditMixin
from app.questions_view import QUESTION_STATES, QuestionsMixin
from app.quests_view import ST_PHOTO, QuestsMixin
from app.rating_view import RatingMixin
from app.registration import RegistrationMixin
from app.review_view import KEEP_STATE as REVIEW_KEEP
from app.review_view import ST_REJECT_REASON, ReviewMixin
from app.school_admin import CLASS_STATES, SchoolAdminMixin
from app.seasons_view import SEASON_STATES, SeasonsMixin
from app.services import audit, school_stats
from app.services import messages as messages_service
from app.services import settings as settings_service
from app.services import shop as shop_service
from app.services import survey as survey_service
from app.services import users as users_service
from app.shop_view import KEEP_STATE as SHOP_KEEP
from app.shop_view import REWARD_STATES, ShopMixin
from app.survey_view import KEEP_STATE as SURVEY_KEEP
from app.survey_view import SURVEY_STATES, SurveyMixin
from app.ui import BROADCAST_INTERVAL, Screen

log = logging.getLogger(__name__)

SECTION_TITLES = {payload: text for items in MENUS.values() for text, payload in items}

# Нажатие кнопки → раздел. Ключ — точный payload или префикс с «:» на конце;
# если подходят несколько ключей, берётся самый длинный («bc:read:» раньше «bc:»).
# Обработчик раздела — метод Bot с параметрами (session, user, payload).
CALLBACK_ROUTES: dict[str, str] = {
    # общее
    "menu:main": "_common_callback",
    "menu:school": "_common_callback",
    "menu:help": "_common_callback",
    "menu:profile": "_common_callback",
    "menu:stats": "_common_callback",
    "cancel": "_common_callback",
    "rem:": "_common_callback",
    "reg:": "_common_callback",
    kb.LOCKED: "_common_callback",
    # квесты ученика
    "menu:quests": "_quests_callback",
    "q:": "_quests_callback",
    # создание и управление квестами
    "menu:new_quest": "_new_quest_callback",
    "nq:": "_new_quest_callback",
    "mq:": "_manage",
    # проверка ответов и архив
    "menu:review": "_review_callback",
    "rev:": "_review_callback",
    "arch:": "_review_callback",
    # рейтинг, соревнование классов, сезоны
    "menu:rating": "_rating_callback",
    "rate:": "_rating_callback",
    "menu:seasons": "_seasons",
    "sz:": "_seasons",
    # вопросы учеников
    "menu:questions": "_questions",
    "qa:": "_questions",
    # школа: классы, учителя, регистрации, журналы
    "menu:classes": "_classes",
    "cls:": "_classes",
    "menu:teachers": "_teachers",
    "tr:": "_teachers",
    "tc:": "_teachers",
    "enl:": "_enrollments",
    "enr:": "_enrollment",
    "log:": "_logs",
    # канал школы
    "pub:": "_publication",
    # достижения, журнал успехов, опрос
    "menu:achievements": "_achievements",
    "ach:": "_achievements",
    "menu:journal": "_journal",
    "jr:": "_journal",
    "sv:": "_survey",
    # монеты, награды, магазин, выдача
    "menu:coins": "_coins",
    "coins:": "_coins",
    "menu:rewards": "_rewards",
    "rw:": "_rewards",
    "menu:shop": "_shop",
    "shop:": "_shop",
    "ord:": "_orders",
    # оповещения
    "menu:broadcast": "_broadcasts",
    "bc:": "_broadcasts",
    "bcd:": "_broadcasts",
    "bc:read:": "_inbox_callback",
    "inbox:": "_inbox_callback",
    # веб-версия
    "web:": "_web_callback",
}

# Кнопки-шаги начатых диалогов: не сбрасывают User.state (остальные — сбрасывают)
KEEP_STATE = (
    NEW_QUEST_KEEP + BROADCAST_KEEP + SHOP_KEEP + COINS_KEEP + REVIEW_KEEP + JOURNAL_KEEP + SURVEY_KEEP
    + ACH_KEEP
)

# Команды: обработчик (session, user, text)
COMMANDS: dict[str, str] = {
    "/cancel": "_cmd_cancel",
    "/start": "_cmd_start",
    "/help": "_cmd_help",
    "/quests": "_cmd_quests",
    "/role": "_cmd_role",
    "/modreview": "_cmd_modreview",
}

# Текст во время диалога: шаг (User.state) → обработчик (session, user, text[, attachments]).
# Третий элемент — нужны ли обработчику вложения (фото, видео).
TEXT_STEPS: tuple[tuple[tuple[str, ...], str, bool], ...] = (
    (NEW_QUEST_STATES, "_new_quest_text", True),
    ((ST_PHOTO,), "_receive_answer", True),
    ((ST_REJECT_REASON,), "_reject_reason_text", False),
    ((ST_PUB_NOTE,), "_publication_note_text", False),
    ((ST_PUB_FIX,), "_publication_fix", True),
    ((ST_QUEST_REWORK,), "_quest_rework_text", False),
    (BROADCAST_STATES, "_broadcast_step", True),
    (REWARD_STATES, "_reward_step", True),
    (COINS_STATES, "_coins_step", False),
    (CLASS_STATES, "_class_step", False),
    (ACH_STATES, "_ach_step", False),
    (SEASON_STATES, "_season_step", False),
    (QUEST_EDIT_STATES, "_quest_edit_step", True),
    (QUESTION_STATES, "_question_step", False),
    (JOURNAL_STATES, "_journal_step", False),
    (SURVEY_STATES, "_survey_text", False),
)


def route_for(payload: str) -> str | None:
    """Имя обработчика раздела для payload кнопки (самый длинный подходящий ключ)."""
    best = None
    for key, handler in CALLBACK_ROUTES.items():
        if payload == key or (key.endswith(":") and payload.startswith(key)):
            if best is None or len(key) > len(best[0]):
                best = (key, handler)
    return best[1] if best else None



def _max_name(user: dict) -> str:
    """«Имя Фамилия» из профиля MAX (фамилии может не быть)."""
    return " ".join(p for p in (user.get("first_name"), user.get("last_name")) if p)


class Bot(
    RegistrationMixin,
    QuestsMixin,
    QuestCreateMixin,
    ReviewMixin,
    RatingMixin,
    BroadcastsMixin,
    CoinsMixin,
    ShopMixin,
    ChannelMixin,
    SchoolAdminMixin,
    JournalMixin,
    AchievementsMixin,
    QuestEditMixin,
    ContestsMixin,
    SeasonsMixin,
    QuestionsMixin,
    SurveyMixin,
    DemoMixin,
):
    def __init__(
        self,
        client: MaxClient,
        sessions: async_sessionmaker[AsyncSession],
        admin_ids: set[int],
        background: bool = True,
        auto_achievements: bool = True,
        demo: bool = False,
    ):
        self.client = client
        self.sessions = sessions
        self.admin_ids = admin_ids
        # Рассылки идут фоном, чтобы бот не замолкал на время отправки.
        # В тестах background=False: рассылка выполняется сразу.
        self.background = background
        # Автодостижения можно выключить: тесты других разделов не ждут бонусов и сообщений
        self.auto_achievements = auto_achievements
        # Демо-режим для жюри: /demo — выбор роли (app/demo.py)
        self.demo = demo
        # Проверка ответов модераторами (настройка в базе), обновляется на каждое событие
        self.moderator_review = False
        self._tasks: set[asyncio.Task] = set()
        # Счётчики на кнопках меню (обновляются на каждое событие пользователя)
        self.menu_counts: dict[str, int] = {}
        # Кнопка «📋 Опрос» в меню: опрос открыт, а пользователь его ещё не прошёл
        self.survey_pending = False
        # Сбои и ошибки — оповещения админам (app/health.py); получатели — ALERT_IDS или все админы
        self.watchdog = health.Watchdog()
        self.alert_ids: set[int] = set()
        # Веб-интерфейс (app/web): адрес и одноразовые пропуски; пусто — кнопки «🌐 Веб-версия» нет
        self.web_url = ""
        self.web_tickets = None

    async def _run_later(self, coro: Coroutine) -> None:
        if not self.background:
            await coro
            return
        task = asyncio.create_task(coro)
        self._tasks.add(task)  # держим ссылку, иначе задачу может собрать сборщик мусора
        task.add_done_callback(self._tasks.discard)

    async def wait_background(self) -> None:
        """Дождаться фоновых рассылок (при остановке бота)."""
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def handle_update(self, update: dict) -> None:
        kind = update.get("update_type")
        try:
            if kind == "bot_started":
                user = update.get("user") or {}
                await self._process_text(user.get("user_id"), _max_name(user), "/start", [])
            elif kind == "message_created":
                await self._on_message(update.get("message") or {})
            elif kind == "message_callback":
                await self._on_callback(update.get("callback") or {})
            elif kind in ("bot_added", "bot_admin_permissions_changed"):
                await self._on_bot_added(update)
            elif kind not in ("bot_stopped", "message_edited", "message_removed"):
                log.info("Необработанное событие %s, поля: %s", kind, sorted(update))
        except Exception as exc:
            log.exception("Ошибка при обработке события %s", kind)
            notice = self.watchdog.error(f"Событие «{kind}»: {type(exc).__name__}: {str(exc)[:200]}")
            if notice:
                await self.alert_admins(notice)

    async def alert_admins(self, text: str) -> None:
        """Служебное оповещение о сбое в личные сообщения: ALERT_IDS или все админы из базы.
        Само никогда не падает: оповещение о сбое не должно создавать новый сбой."""
        try:
            if self.alert_ids:
                ids = sorted(self.alert_ids)
            else:
                async with self.sessions() as session:
                    ids = [u.max_user_id for u in await users_service.admins(session)]
            for user_id in ids:
                try:
                    await self._send(user_id, text, kb.back_to_menu())
                except httpx.HTTPError:
                    log.warning("Не удалось отправить оповещение о сбое %s", user_id)
        except Exception:
            log.exception("Не удалось разослать оповещение о сбое")

    async def _send(
        self, user_id: int, text: str, attachments: list[dict] | None = None
    ) -> None:
        """Отправляет сообщение и запоминает его id для перезапуска."""
        if demo_service.is_fake(user_id):
            # Вымышленный пользователь обезличенной базы: в MAX его нет
            return
        result = await self.client.send_message(user_id, text, attachments)
        mid = (((result or {}).get("message") or {}).get("body") or {}).get("mid")
        if mid:
            async with self.sessions() as session:
                await messages_service.remember(session, user_id, mid)

    # --- события ---

    async def _on_message(self, message: dict) -> None:
        sender = message.get("sender") or {}
        if sender.get("is_bot"):
            return
        body = message.get("body") or {}
        if not (body.get("text") or "").strip():
            # Сообщение без текста (фото, голосовое…): какие поля и вложения прислал MAX
            log.info(
                "Сообщение без текста: поля %s, тело %s, вложения %s",
                sorted(message), sorted(body),
                [(a.get("type"), sorted(a.get("payload") or {})) for a in body.get("attachments") or []],
            )
        link = message.get("link") or {}
        if link.get("type") == "forward" and not link.get("chat_id"):
            log.info("Пересланное сообщение без chat_id, поля: %s", sorted(link))
        if link.get("type") == "forward" and link.get("chat_id") and sender.get("user_id"):
            # Админ переслал пост из канала — подключить канал школы
            async with self.sessions() as session:
                user = await session.scalar(select(User).where(User.max_user_id == sender["user_id"]))
                screen = user and await self._channel_from_forward(session, user, link)
            if screen:
                await self._send(sender["user_id"], *screen)
                return
        body = message.get("body") or {}
        await self._process_text(
            sender.get("user_id"),
            _max_name(sender),
            (body.get("text") or "").strip(),
            body.get("attachments") or [],
        )

    async def _process_text(
        self, user_id: int | None, name: str, text: str, attachments: list[dict]
    ) -> None:
        if user_id is None:
            return
        if text.startswith("/id"):
            await self._send(user_id, f"Ваш MAX user_id: {user_id}")
            return
        async with self.sessions() as session:
            user = await users_service.get_or_create_user(
                session, user_id, name, self.admin_ids
            )
            self.moderator_review = await settings_service.moderator_review(session)
            await self._refresh_menu_counts(session, user)
            if self.demo and user.full_name is None:
                screen = await self._demo_welcome(session, user)
            elif self.demo and text.startswith("/demo"):
                screen = self._demo_screen(user)
            else:
                screen = await self._route_text(session, user, text, attachments)
        if screen:
            await self._send(user_id, *screen)

    async def _on_callback(self, callback: dict) -> None:
        user = callback.get("user") or {}
        user_id = user.get("user_id")
        callback_id = callback.get("callback_id")
        if user_id is None or not callback_id:
            return
        payload = callback.get("payload") or ""
        if payload == "menu:restart":
            # Сначала отвечаем на нажатие: сообщение с кнопкой сейчас будет удалено
            await self.client.answer_callback(callback_id, notification="Перезапуск…")
            await self._restart(user_id, _max_name(user))
            return
        notification: str | None = None
        async with self.sessions() as session:
            me = await users_service.get_or_create_user(
                session, user_id, _max_name(user), self.admin_ids
            )
            self.moderator_review = await settings_service.moderator_review(session)
            await self._refresh_menu_counts(session, me)
            if self.demo and payload.startswith("demo:"):
                result = await self._demo_callback(session, me, payload)
            elif self.demo and me.full_name is None:
                result = await self._demo_welcome(session, me)
            elif me.full_name is None:
                result = await self._registration_callback(session, me, payload)
            else:
                result = await self._route_callback(session, me, payload)
            screen: Screen | None
            screen, notification = (None, result) if isinstance(result, str) else (result, None)
        if screen:
            await self.client.answer_callback(
                callback_id, text=screen[0], attachments=screen[1] or []
            )
        else:
            await self.client.answer_callback(callback_id, notification=notification or "Готово")

    # --- маршрутизация ---

    async def _route_text(
        self, session: AsyncSession, user: User, text: str, attachments: list[dict]
    ) -> Screen | None:
        if user.full_name is None:
            return await self._registration_text(session, user, text)
        command = COMMANDS.get(text.split(maxsplit=1)[0]) if text.startswith("/") else None
        if command:
            return await getattr(self, command)(session, user, text)
        for states, handler, with_attachments in TEXT_STEPS:
            if user.state in states:
                step = getattr(self, handler)
                if with_attachments:
                    return await step(session, user, text, attachments)
                return await step(session, user, text)
        return self._menu(user)

    async def _route_callback(
        self, session: AsyncSession, user: User, payload: str
    ) -> Screen | str:
        """Экран, которым заменить сообщение, или текст всплывающего уведомления."""
        # Любая кнопка, кроме шагов начатого диалога, прерывает его
        if user.state and not payload.startswith(KEEP_STATE):
            await users_service.set_state(session, user, None)
        handler = route_for(payload)
        if handler:
            return await getattr(self, handler)(session, user, payload)
        title = SECTION_TITLES.get(payload, "Раздел")
        return f"«{title}» скоро будет доступен"

    async def _common_callback(self, session: AsyncSession, user: User, payload: str) -> Screen | str:
        """Главное меню, справка, профиль, статистика и служебные кнопки."""
        if payload == "menu:main":
            return self._menu(user)
        if payload == "cancel":
            return self._menu(user, "Действие отменено.")
        if payload == "menu:school":
            return self._school_menu(user)
        if payload == "menu:help":
            return await self._help(session, user)
        if payload == "menu:profile":
            return await self._profile(session, user)
        if payload == "menu:stats":
            return await self._stats(session, user)
        if payload in ("rem:off", "rem:on"):
            return await self._reminders_toggle(session, user, payload == "rem:off")
        if payload == kb.LOCKED:
            return "🔒 Недоступно модератору: это делает админ."
        if payload.startswith("reg:"):
            return "Вы уже зарегистрированы."
        return "Неизвестное действие."

    async def _web_callback(self, session: AsyncSession, user: User, payload: str) -> Screen | str:
        """Одноразовая ссылка на веб-версию: действует 10 минут и только для того, кто нажал."""
        if not (self.web_url and self.web_tickets):
            return "Веб-версия пока не подключена."
        link = f"{self.web_url.rstrip('/')}/?t={self.web_tickets.issue(user.max_user_id)}"
        what = "профиль, успехи и рейтинг" if user.role == Role.student else "пульс любого класса и рейтинг"
        return (
            f"🌐 Веб-версия: {what} на одной странице.\n"
            "Ссылка личная и действует 10 минут — не пересылайте её.",
            kb.web_link(link),
        )

    # --- команды ---

    async def _cmd_cancel(self, session: AsyncSession, user: User, text: str) -> Screen:
        await users_service.set_state(session, user, None)
        return self._menu(user, "Действие отменено.")

    async def _cmd_start(self, session: AsyncSession, user: User, text: str) -> Screen:
        await users_service.set_state(session, user, None)
        return self._menu(user)

    async def _cmd_help(self, session: AsyncSession, user: User, text: str) -> Screen:
        await users_service.set_state(session, user, None)
        return await self._help(session, user)

    async def _cmd_quests(self, session: AsyncSession, user: User, text: str) -> Screen:
        await users_service.set_state(session, user, None)
        return await self._quest_list(session, user)

    # --- меню и регистрация ---

    async def _restart(self, user_id: int, name: str) -> None:
        """Сбрасывает начатое действие, удаляет сообщения бота и присылает меню.

        Сообщения пользователя бот удалить не может: API MAX этого не позволяет.
        """
        async with self.sessions() as session:
            user = await users_service.get_or_create_user(
                session, user_id, name, self.admin_ids
            )
            await users_service.set_state(session, user, None)
            mids = await messages_service.pop_all(session, user_id)
        for mid in mids:
            try:
                await self.client.delete_message(mid)
            except httpx.HTTPError:
                # Слишком старое или уже удалённое сообщение — пропускаем
                log.info("Не удалось удалить сообщение %s", mid)
        async with self.sessions() as session:
            user = await users_service.get_or_create_user(
                session, user_id, name, self.admin_ids
            )
            if user.full_name is None:
                screen: Screen = await self._registration_screen(session, user)
            else:
                screen = self._menu(user)
        await self._send(user_id, *screen)

    def _menu(self, user: User, prefix: str = "") -> Screen:
        lines = [prefix, ""] if prefix else []
        lines.append(f"Привет, {user.display_name}!")
        lines.append(f"Ваша роль: {ROLE_TITLES[user.role]}")
        if user.role == Role.teacher:
            names = ", ".join(c.name for c in user.teacher_classes)
            lines.append(f"Классы: {names or 'пока не назначены'}")
        elif user.school_class:
            lines.append(f"Класс: {user.school_class.name}")
        if user.role == Role.student:
            lines.append(f"Баланс: {coins(user.balance)}")
        if self.demo:
            lines.append("🧪 Демо-версия: /demo — сменить роль")
        lines.append("Выберите раздел:")
        return "\n".join(lines), kb.menu_for(
            user.role, self.moderator_review, help_first=not user.help_seen, counts=self.menu_counts,
            survey=self.survey_pending, web=bool(self.web_url and self.web_tickets),
        )

    async def _refresh_menu_counts(self, session: AsyncSession, user: User) -> None:
        """Счётчики на кнопках меню: что ждёт действия в разделе."""
        self.menu_counts = {}
        self.survey_pending = False
        if user.full_name is None:
            return
        self.survey_pending = await survey_service.pending(session, user)
        if user.role in (Role.admin, Role.moderator):
            self.menu_counts = {
                "menu:rewards": len(await shop_service.pending_for(session, user)),
                "menu:stats": await school_stats.unchecked_count(session),
            }
        elif user.role == Role.teacher:
            self.menu_counts = {"ord:list": len(await shop_service.pending_for(session, user))}

    def _school_menu(self, user: User) -> Screen | str:
        if user.role not in (Role.admin, Role.moderator):
            return "Раздел доступен админам и модераторам."
        return "🏫 Школа\nКлассы и ученики, учителя и их заявки.", kb.school_menu()

    async def _help(self, session: AsyncSession, user: User) -> Screen:
        """«ℹ️ Помощь» и /help: справка по меню своей роли."""
        if not user.help_seen:
            user.help_seen = True  # дальше кнопка уходит в конец меню
            await session.commit()
        return help_texts.help_text(user.role), kb.back_to_menu()

    async def _cmd_role(self, session: AsyncSession, me: User, text: str) -> Screen:
        """/role <max_user_id> <admin|moderator|teacher|student> — только для админов."""
        if me.role != Role.admin:
            return "Команда доступна только админам.", None
        parts = text.split()
        try:
            target_id = int(parts[1])
            role = Role(parts[2])
        except (IndexError, ValueError):
            return "Формат: /role <user_id> <admin|moderator|teacher|student>", None
        target = await users_service.set_role(session, target_id, role)
        if target is None:
            return "Пользователь не найден: он должен сначала написать боту /start.", None
        audit.record(me, "role.set", f"{target.display_name} (max {target_id}) → {role.value}")
        text = f"Готово: {target.display_name} ({target_id}) теперь {ROLE_TITLES[role]}."
        if role == Role.teacher:
            text += "\nНазначьте ему классы в разделе «👩‍🏫 Учителя»."
        elif role == Role.moderator:
            text += (
                "\nУ него меню модератора: без монет, наград, проверки ответов и ролей; "
                "оповещения — через ваше подтверждение. Действия пишутся в журнал на сервере."
            )
        elif role == Role.student and target.full_name is None:
            text += "\nКласса у него нет: при следующем сообщении боту он выберет класс."
        return text, kb.back_to_menu()

    async def _cmd_modreview(self, session: AsyncSession, me: User, text: str) -> Screen:
        """/modreview on|off — временно разрешить модераторам проверять ответы."""
        if me.role != Role.admin:
            return "Команда доступна только админам.", None
        parts = text.split()
        enabled = await settings_service.moderator_review(session)
        if len(parts) < 2:
            state = "включена ✅" if enabled else "выключена"
            return (
                f"Проверка ответов модераторами сейчас {state}.\n"
                "Включить: /modreview on\nВыключить: /modreview off",
                None,
            )
        if parts[1] not in ("on", "off"):
            return "Формат: /modreview on или /modreview off", None
        value = parts[1] == "on"
        await settings_service.set_flag(session, settings_service.MODERATOR_REVIEW, value)
        self.moderator_review = value
        audit.record(me, "settings.moderator_review", "включена" if value else "выключена")
        if value:
            notice = (
                "📋 Администратор временно разрешил модераторам проверять ответы на квесты. "
                "Раздел «На проверке» открыт в меню. О каждом вашем решении узнаёт админ "
                "и может его изменить."
            )
        else:
            notice = (
                "📋 Проверка ответов снова за учителями и администратором: "
                "раздел «На проверке» для модераторов закрыт. Спасибо за помощь!"
            )
        await self._notify(await users_service.moderators(session), notice, kb.back_to_menu())
        if value:
            return (
                "✅ Модераторы теперь проверяют ответы. О каждом их решении вам придёт "
                "сообщение с кнопкой «Отменить». Выключить: /modreview off",
                kb.back_to_menu(),
            )
        return "Проверка ответов модераторами выключена.", kb.back_to_menu()

    # --- массовая рассылка (её используют разные разделы) ---

    async def _broadcast(
        self, user_ids: list[int], text: str, attachments: list[dict] | None = None
    ) -> None:
        for i, user_id in enumerate(user_ids):
            if i:
                await asyncio.sleep(BROADCAST_INTERVAL)
            try:
                await self._send(user_id, text, attachments)
            except Exception:
                log.warning("Не удалось отправить рассылку пользователю %s", user_id)
