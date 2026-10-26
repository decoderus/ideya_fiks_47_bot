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

from app import health, help_texts
from app import keyboards as kb
from app.db.models import (
    OrderStatus,
    Quest,
    QuestKind,
    DraftStatus,
    QuestRepeat,
    Reward,
    RewardKind,
    Role,
    SchoolClass,
    Submission,
    SubmissionStatus,
    User,
)
from app.keyboards import MENUS, ROLE_TITLES, coins
from app.max_api import MaxClient
from app.achievements import ACH_STATES, AchievementsMixin
from app.channel import ST_PUB_FIX, ST_PUB_NOTE, ChannelMixin
from app import demo as demo_service
from app.contests import ContestsMixin
from app.demo_view import DemoMixin
from app.journal import JOURNAL_STATES, JournalMixin
from app.seasons_view import SEASON_STATES, SeasonsMixin
from app.quest_edit import QUEST_EDIT_STATES, QuestEditMixin
from app.questions_view import QUESTION_STATES, QuestionsMixin
from app.survey_view import SURVEY_STATES, SurveyMixin
from app.registration import RegistrationMixin
from app.school_admin import CLASS_STATES, SchoolAdminMixin
from app.services import broadcasts as broadcasts_service
from app.services import classes as classes_service
from app.services import coins as coins_service
from app.services import contests as contests_service
from app.services import messages as messages_service
from app.services import quests as quests_service
from app.services import audit
from app.services import publication as pub_service
from app.services import questions as questions_service
from app.services import quiz as quiz_service
from app.services import rating as rating_service
from app.services import school_stats
from app.services import settings as settings_service
from app.services import seasons as seasons_service
from app.services import shop as shop_service
from app.services import survey as survey_service
from app.services import users as users_service
from app.services.broadcasts import BroadcastError
from app.services.questions import QuestionError
from app.services.coins import CoinsError
from app.services.quests import QuestError
from app.services.shop import ShopError
from app.ui import (
    Screen,
    find_attachment as _find_attachment,
    find_image as _find_image,
    format_date as _format_date,
    format_day as _format_day,
    broadcast_media as _broadcast_media,
    image_attachment as _image_attachment,
    parse_id as _parse_id,
    video_attachment as _video_attachment,
)

log = logging.getLogger(__name__)

SECTION_TITLES = {payload: text for items in MENUS.values() for text, payload in items}

# Пауза между сообщениями массовой рассылки: общий лимит MAX — 30 запросов в секунду
BROADCAST_INTERVAL = 0.05

RATING_TOP = 10
MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}

# Состояния диалога (User.state)
ST_NQ_TITLE = "nq_title"
ST_NQ_DESC = "nq_desc"
ST_NQ_REWARD = "nq_reward"
ST_NQ_KIND = "nq_kind"
ST_NQ_REPEAT = "nq_repeat"
ST_NQ_PHOTO = "nq_photo"  # последний шаг: картинка или «без картинки»
ST_NQ_AUDIENCE = "nq_audience"  # только у админа: для кого квест
ST_PHOTO = "photo"
ST_REJECT_REASON = "reject_reason"  # проверяющий пишет причину отклонения
ST_QUEST_REWORK = "quest_rework"  # админ пишет, что доработать в квесте модератора
ST_BC_TEXT = "bc_text"
ST_BC_AUDIENCE = "bc_audience"
ST_BC_CONFIRM = "bc_confirm"
# Награды: создание и правка (только админ)
ST_RW_TITLE = "rw_title"
ST_RW_DESC = "rw_desc"
ST_RW_PRICE = "rw_price"
ST_RW_STOCK = "rw_stock"
ST_RW_PHOTO = "rw_photo"
ST_RW_EDIT_PRICE = "rw_edit_price"
ST_RW_EDIT_STOCK = "rw_edit_stock"
# Монеты: ручное начисление и списание
ST_COINS_AMOUNT = "coins_amount"
ST_COINS_REASON = "coins_reason"
ST_COINS_CONFIRM = "coins_confirm"
COINS_STATES = (ST_COINS_AMOUNT, ST_COINS_REASON, ST_COINS_CONFIRM)
REWARD_STATES = (
    ST_RW_TITLE,
    ST_RW_DESC,
    ST_RW_PRICE,
    ST_RW_STOCK,
    ST_RW_PHOTO,
    ST_RW_EDIT_PRICE,
    ST_RW_EDIT_STOCK,
)



def _max_name(user: dict) -> str:
    """«Имя Фамилия» из профиля MAX (фамилии может не быть)."""
    return " ".join(p for p in (user.get("first_name"), user.get("last_name")) if p)


class Bot(
    RegistrationMixin,
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
        if text.startswith("/cancel"):
            await users_service.set_state(session, user, None)
            return self._menu(user, "Действие отменено.")
        if text.startswith("/start"):
            await users_service.set_state(session, user, None)
            return self._menu(user)
        if text.startswith("/help"):
            await users_service.set_state(session, user, None)
            return await self._help(session, user)
        if text.startswith("/role"):
            return await self._cmd_role(session, user, text)
        if text.startswith("/modreview"):
            return await self._cmd_modreview(session, user, text)
        if text.startswith("/quests"):
            await users_service.set_state(session, user, None)
            return await self._quest_list(session, user)
        if user.state == ST_NQ_PHOTO:
            return await self._new_quest_photo(session, user, attachments)
        if user.state in (
            ST_NQ_TITLE, ST_NQ_DESC, ST_NQ_REWARD, ST_NQ_KIND, ST_NQ_REPEAT, ST_NQ_AUDIENCE
        ):
            return await self._new_quest_step(session, user, text)
        if user.state == ST_PHOTO:
            return await self._receive_answer(session, user, text, attachments)
        if user.state == ST_REJECT_REASON:
            if not text or text.startswith("/"):
                return "Напишите причину текстом или выберите кнопку под заявкой.", None
            submission_id = (user.state_data or {}).get("submission_id", 0)
            await users_service.set_state(session, user, None)
            result = await self._review_decide(session, user, submission_id, False, comment=text)
            return (result, None) if isinstance(result, str) else result
        if user.state == ST_PUB_NOTE:
            return await self._publication_note_text(session, user, text)
        if user.state == ST_PUB_FIX:
            return await self._publication_fix(session, user, text, attachments)
        if user.state == ST_QUEST_REWORK:
            if not text or text.startswith("/"):
                return "Напишите комментарий текстом или нажмите «Вернуть без комментария».", None
            quest_id = (user.state_data or {}).get("quest_id", 0)
            result = await self._quest_return(session, user, quest_id, text)
            return (result, None) if isinstance(result, str) else result
        if user.state in (ST_BC_TEXT, ST_BC_AUDIENCE, ST_BC_CONFIRM):
            return await self._broadcast_step(session, user, text, attachments)
        if user.state in REWARD_STATES:
            return await self._reward_step(session, user, text, attachments)
        if user.state in COINS_STATES:
            return await self._coins_step(session, user, text)
        if user.state in CLASS_STATES:
            return await self._class_step(session, user, text)
        if user.state in ACH_STATES:
            return await self._ach_step(session, user, text)
        if user.state in SEASON_STATES:
            return await self._season_step(session, user, text)
        if user.state in QUEST_EDIT_STATES:
            return await self._quest_edit_step(session, user, text, attachments)
        if user.state in QUESTION_STATES:
            return await self._question_step(session, user, text)
        if user.state in JOURNAL_STATES:
            return await self._journal_step(session, user, text)
        if user.state in SURVEY_STATES:
            return await self._survey_text(session, user, text)
        return self._menu(user)

    async def _route_callback(
        self, session: AsyncSession, user: User, payload: str
    ) -> Screen | str:
        """Экран, которым заменить сообщение, или текст всплывающего уведомления."""
        # Любая кнопка, кроме шагов создания квеста и оповещения, прерывает начатый диалог
        if user.state and not payload.startswith(
            ("nq:", "bc:aud:", "bc:send", "rw:stock:", "rw:photo:", "coins:ok", "ach:emoji:", "rev:nr:", "rev:nw:",
             "jr:cat:", "jr:coins:", "sv:a:", "sv:skip")
        ):
            await users_service.set_state(session, user, None)

        if payload == "menu:main":
            return self._menu(user)
        if payload == "menu:school":
            return self._school_menu(user)
        if payload == "menu:help":
            return await self._help(session, user)
        if payload == "cancel":
            return self._menu(user, "Действие отменено.")
        if payload == "menu:quests":
            return await self._quest_list(session, user)
        if payload.startswith("q:open:"):
            return await self._quest_card(session, user, _parse_id(payload))
        if payload.startswith("q:done:"):
            return await self._quest_done(session, user, _parse_id(payload))
        if payload.startswith("q:quiz:"):
            return await self._quiz_answer(session, user, payload)
        if payload.startswith("q:photo:"):
            return await self._quest_photo(session, user, _parse_id(payload))
        if payload.startswith("qa:") or payload == "menu:questions":
            return await self._questions(session, user, payload)
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
        if payload == "menu:review":
            return await self._review_list(session, user)
        if payload.startswith("rev:open:"):
            return await self._review_open(session, user, _parse_id(payload))
        if payload.startswith("rev:flip:"):
            return await self._review_override(session, user, _parse_id(payload))
        if payload.startswith("rev:no:"):
            return await self._reject_prompt(session, user, _parse_id(payload) or 0)
        if payload.startswith("rev:nw:"):
            return await self._reject_write(session, user, _parse_id(payload) or 0)
        if payload.startswith("rev:back:"):
            return await self._review_back(session, user, _parse_id(payload) or 0)
        if payload.startswith("rev:nr:"):
            ids = payload.split(":")
            try:
                submission_id, choice = int(ids[2]), int(ids[3])
            except (IndexError, ValueError):
                return "Неизвестное действие."
            await users_service.set_state(session, user, None)
            reasons = quests_service.REJECT_REASONS
            reason = reasons[choice - 1] if 1 <= choice <= len(reasons) else None
            return await self._review_decide(session, user, submission_id, False, comment=reason)
        if payload.startswith(("rev:ok:", "rev:no:")):
            parts = payload.split(":")
            try:
                submission_id = int(parts[2])
                amount = int(parts[3]) if len(parts) > 3 else None
            except (IndexError, ValueError):
                return "Неизвестное действие."
            return await self._review_decide(
                session, user, submission_id, payload.startswith("rev:ok:"), amount
            )
        if payload in ("menu:rating", "rate:school"):
            return await self._rating(session, user, None)
        if payload == "menu:seasons" or payload.startswith("sz:"):
            return await self._seasons(session, user, payload)
        if payload == "rate:contest":
            return await self._contest(session, user)
        if payload.startswith("rate:class:"):
            return await self._rating(session, user, _parse_id(payload))
        if payload == "rate:classes":
            return await self._rating_classes(session, user)
        if payload.startswith("mq:"):
            return await self._manage(session, user, payload)
        if payload.startswith("pub:"):
            return await self._publication(session, user, payload)
        if payload == "menu:classes" or payload.startswith("cls:"):
            return await self._classes(session, user, payload)
        if payload.startswith("enl:"):
            return await self._enrollments(session, user, payload)
        if payload == kb.LOCKED:
            return "🔒 Недоступно модератору: это делает админ."
        if payload in ("rem:off", "rem:on"):
            return await self._reminders_toggle(session, user, payload == "rem:off")
        if payload == "menu:profile":
            return await self._profile(session, user)
        if payload == "menu:achievements" or payload.startswith("ach:"):
            return await self._achievements(session, user, payload)
        if payload == "menu:journal" or payload.startswith("jr:"):
            return await self._journal(session, user, payload)
        if payload == "menu:stats":
            return await self._stats(session, user)
        if payload.startswith("sv:"):
            return await self._survey(session, user, payload)
        if payload.startswith("log:"):
            return await self._logs(session, user, payload)
        if payload == "menu:teachers" or payload.startswith(("tr:", "tc:")):
            return await self._teachers(session, user, payload)
        if payload.startswith("enr:"):
            return await self._enrollment(session, user, payload)
        if payload.startswith("reg:"):
            return "Вы уже зарегистрированы."
        if payload == "menu:coins" or payload.startswith("coins:"):
            return await self._coins(session, user, payload)
        if payload == "menu:rewards" or payload.startswith("rw:"):
            return await self._rewards(session, user, payload)
        if payload == "menu:shop" or payload.startswith("shop:"):
            return await self._shop(session, user, payload)
        if payload.startswith("ord:"):
            return await self._orders(session, user, payload)
        if payload.startswith("inbox:open:"):
            return await self._inbox_open(session, user, _parse_id(payload) or 0)
        if payload.startswith("inbox:"):
            return await self._inbox(session, user, max(_parse_id(payload) or 0, 0))
        if payload.startswith("bc:read:"):
            return await self._broadcast_read(session, user, _parse_id(payload) or 0)
        if payload == "menu:broadcast" or payload.startswith(("bc:", "bcd:")):
            return await self._broadcasts(session, user, payload)
        if payload.startswith("arch:page:"):
            return await self._archive_list(session, user, _parse_id(payload) or 0)
        if payload.startswith("arch:daily:"):
            return await self._archive_list(session, user, _parse_id(payload) or 0, daily=True)
        if payload.startswith("arch:open:"):
            return await self._archive_open(session, user, _parse_id(payload))
        title = SECTION_TITLES.get(payload, "Раздел")
        return f"«{title}» скоро будет доступен"

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
            survey=self.survey_pending,
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

    # --- квесты ученика ---

    async def _quest_list(self, session: AsyncSession, user: User) -> Screen:
        items = await quests_service.available_quests(session, user)
        if not items:
            return "Сейчас нет доступных квестов. Загляните позже!", kb.back_to_menu()
        text = f"🎯 Доступные квесты (награда в монетах).\nБаланс: {coins(user.balance)}"
        if any(pending for _, pending in items):
            text += "\n⏳ — ждёт проверки учителем"
        return text, kb.quest_list(items)

    async def _quest_card(
        self, session: AsyncSession, user: User, quest_id: int | None
    ) -> Screen | str:
        try:
            quest = await quests_service.get_open_quest(session, user, quest_id or 0)
        except QuestError as exc:
            return await self._quest_info(session, user, quest_id or 0, str(exc))
        if quest.kind == QuestKind.quiz:
            return await self._quiz_card(session, user, quest)
        hint = (
            "Когда выполните, нажмите «Выполнено»."
            if quest.kind == QuestKind.auto
            else "Выполните задание и отправьте ответ на проверку: текстом, фото или видео."
        )
        text = f"🎯 {quest.title}\n\n"
        if quest.description:
            text += f"{quest.description}\n\n"
        text += (
            f"Награда: {kb.reward_text(quest)}\n"
            f"{self._range_line(quest)}{self._repeat_line(quest)}{self._author_line(quest)}"
            f"\n\n{hint}"
        )
        return text, self._quest_image(quest) + kb.quest_card(quest)

    async def _quest_info(
        self, session: AsyncSession, user: User, quest_id: int, status: str
    ) -> Screen | str:
        """Квест, который сейчас не выполнить (на проверке, уже выполнен): статус и вопрос."""
        try:
            quest = await questions_service.quest_for_question(session, user, quest_id)
        except QuestionError:
            return status
        text = f"🎯 {quest.title}\n\n"
        if quest.description:
            text += f"{quest.description}\n\n"
        text += f"ℹ️ {status}\n\nЕсть вопрос по заданию или по проверке — задайте его."
        return text, self._quest_image(quest) + kb.quest_info(quest.id)

    async def _quiz_card(self, session: AsyncSession, user: User, quest) -> Screen:
        question = await quiz_service.today_question(session, user, quest)
        text = f"🧠 {quest.title}\n\n"
        if quest.description:
            text += f"{quest.description}\n\n"
        text += (
            f"Награда за верный ответ: {kb.reward_text(quest)}\n"
            f"{self._repeat_line(quest)}\n"
            f"📚 {question.subject}\n{question.text}\n\n"
            "Выберите ответ — попытка одна:"
        )
        return text, self._quest_image(quest) + kb.quiz_options(quest.id, question)

    async def _quiz_answer(self, session: AsyncSession, user: User, payload: str) -> Screen | str:
        try:
            _, _, quest_id, question_index, chosen = payload.split(":")
            result = await quiz_service.answer(
                session, user, int(quest_id), int(question_index), int(chosen)
            )
        except ValueError:
            return "Неизвестное действие."
        except QuestError as exc:
            return str(exc)
        q, quest = result.question, result.submission.quest
        letters = quiz_service.LETTERS
        lines = [f"🧠 {q.subject}: {q.text}", ""]
        if result.correct:
            lines.append(f"✅ Верно: {letters[q.correct]}. {q.options[q.correct]}")
            lines.append(f"+{coins(quest.reward)}. Баланс: {coins(user.balance)}")
            await self._check_achievements(session, user)
        else:
            lines.append(f"❌ Ваш ответ: {letters[result.chosen]}. {q.options[result.chosen]}")
            lines.append(f"Правильно: {letters[q.correct]}. {q.options[q.correct]}")
        lines += ["", "Новый вопрос — завтра. Серия считается по дням с верным ответом."]
        return "\n".join(lines), kb.to_quests()

    @staticmethod
    def _quest_image(quest) -> list[dict]:
        return [_image_attachment(quest.photo)] if quest.photo else []

    @staticmethod
    def _range_line(quest) -> str:
        if quest.reward_min is None:
            return ""
        return "💡 Точную сумму назначит проверяющий: чем значимее результат, тем больше.\n"

    @staticmethod
    def _repeat_line(quest) -> str:
        """«🔁 Можно выполнять раз в день» с переводом строки, пусто для разового квеста."""
        if quest.repeat == QuestRepeat.none:
            return ""
        return f"🔁 Можно выполнять {kb.REPEAT_TITLES[quest.repeat].lower()}\n"

    @staticmethod
    def _again_note(quest) -> str:
        if quest.repeat == QuestRepeat.none:
            return ""
        return f"\n🔁 Снова выполнить можно {quests_service.NEXT_TIME[quest.repeat]}."

    @staticmethod
    def _author_line(quest) -> str:
        author = quest.author
        return f"Автор: {author.display_name}, {ROLE_TITLES[author.role]}"

    async def _quest_done(
        self, session: AsyncSession, user: User, quest_id: int | None
    ) -> Screen:
        try:
            quest = await quests_service.complete_auto(session, user, quest_id or 0)
        except QuestError as exc:
            return str(exc), kb.to_quests()
        await self._check_achievements(session, user)
        return (
            f"✅ Квест «{quest.title}» выполнен! +{coins(quest.reward)}\n"
            f"Баланс: {coins(user.balance)}{self._again_note(quest)}",
            kb.to_quests(),
        )

    async def _quest_photo(
        self, session: AsyncSession, user: User, quest_id: int | None
    ) -> Screen | str:
        try:
            quest = await quests_service.get_open_quest(session, user, quest_id or 0)
        except QuestError as exc:
            return str(exc)
        if quest.kind != QuestKind.photo:
            return "Этот квест не требует ответа на проверку."
        await users_service.set_state(session, user, ST_PHOTO, {"quest_id": quest.id})
        return (
            f"📝 «{quest.title}»\n"
            "Пришлите ответ одним сообщением: текстом, фото или видео "
            "(к фото и видео можно добавить подпись).",
            kb.cancel(),
        )

    async def _receive_answer(
        self, session: AsyncSession, user: User, text: str, attachments: list[dict]
    ) -> Screen:
        photo = _find_image(attachments)
        video = _find_attachment(attachments, "video")
        if photo is None and video is None and not text:
            return (
                "Пришлите ответ текстом, фото или видео (не файлом) или нажмите «Отмена».",
                kb.cancel(),
            )
        if len(text) > quests_service.ANSWER_MAX:
            return (
                f"Слишком длинный ответ: {len(text)} символов, "
                f"максимум {quests_service.ANSWER_MAX}. Сократите и пришлите снова.",
                kb.cancel(),
            )
        quest_id = (user.state_data or {}).get("quest_id", 0)
        await users_service.set_state(session, user, None)
        try:
            submission = await quests_service.submit_answer(
                session, user, quest_id, photo, text, video
            )
        except QuestError as exc:
            return str(exc), kb.to_quests()
        await self._notify_reviewers(session, submission)
        return (
            "📨 Ответ отправлен на проверку. Монеты придут после подтверждения учителем.",
            kb.to_quests(),
        )

    async def _notify_reviewers(self, session: AsyncSession, submission: Submission) -> None:
        quest = submission.quest
        text = (
            f"📋 Новая заявка на проверку\n"
            f"{submission.user.display_name} — «{quest.title}» ({kb.reward_text(quest)})"
        )
        for reviewer in await quests_service.reviewers_for(session, submission):
            if reviewer.id == submission.user_id:
                continue
            try:
                await self._send(
                    reviewer.max_user_id, text, kb.open_review(submission.id)
                )
            except httpx.HTTPError:
                log.warning("Не удалось уведомить проверяющего %s", reviewer.max_user_id)

    # --- рейтинг ---

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

    # --- рассылки ---

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

    # --- оповещения ---

    @staticmethod
    def _announcement(text: str, author: User | None = None, to: str | None = None) -> str:
        """Оповещение, как его видит получатель: от кого и кому, затем текст."""
        head = "📣 Оповещение"
        if author is not None:
            head += f"\nОт: {author.display_name} ({ROLE_TITLES[author.role]})"
        if to:
            head += f"\nКому: {to}"
        return f"{head}\n\n{text}" if text else head

    async def _broadcast_text(self, session: AsyncSession, broadcast) -> str:
        to = await broadcasts_service.reader_label(session, broadcast.audience, broadcast.class_id)
        return self._announcement(broadcast.text, broadcast.author, to)

    async def _broadcasts(self, session: AsyncSession, user: User, payload: str) -> Screen | str:
        if payload.startswith("bcd:"):
            return await self._draft_decision(session, user, payload)
        if not broadcasts_service.can_prepare(user):
            return "Оповещения доступны только админам и модераторам."
        if payload == "menu:broadcast":
            if user.role == Role.moderator:
                return (
                    "📣 Оповещения\nПодготовьте сообщение: всем, ученикам, учителям, "
                    "модераторам, одному классу или лично ученику, учителю или модератору. "
                    "Оно уйдёт только после подтверждения администратора.",
                    kb.broadcast_menu(draft=True),
                )
            return (
                "📣 Оповещения\nОтправьте сообщение: всем, ученикам, учителям, "
                "модераторам, одному классу или лично ученику, учителю или модератору.",
                kb.broadcast_menu(),
            )
        if payload == "bc:new":
            await users_service.set_state(session, user, ST_BC_TEXT, {})
            return (
                "✍️ Новое оповещение. Шаг 1 из 3.\n"
                "Напишите текст (можно с одним фото) или запишите голосовое сообщение.",
                kb.cancel(),
            )
        if payload.startswith("bc:aud:"):
            return await self._broadcast_audience(session, user, payload[len("bc:aud:"):])
        if payload == "bc:send":
            return await self._broadcast_send(session, user)
        if payload.startswith("bc:history:"):
            return await self._broadcast_history(session, max(_parse_id(payload) or 0, 0))
        if payload.startswith("bc:open:"):
            return await self._broadcast_open(session, user, _parse_id(payload) or 0)
        return "Неизвестное действие."

    async def _broadcast_step(
        self, session: AsyncSession, user: User, text: str, attachments: list[dict]
    ) -> Screen:
        if user.state == ST_BC_AUDIENCE:
            return "Выберите кнопкой ниже, кому отправить.", kb.broadcast_audience()
        if user.state == ST_BC_CONFIRM:
            return (
                "Нажмите «Отправить» или «Отмена».",
                kb.broadcast_confirm(draft=user.role == Role.moderator),
            )
        # ST_BC_TEXT
        photo = _find_image(attachments)
        audio = _find_attachment(attachments, "audio")
        if attachments and photo is None and audio is None:
            log.info("Оповещение: неподдерживаемые вложения %s", [a.get("type") for a in attachments])
        if not text and photo is None and audio is None:
            return (
                "Пришлите текст оповещения, фото или голосовое сообщение или нажмите «Отмена».",
                kb.cancel(),
            )
        if len(text) > broadcasts_service.TEXT_MAX:
            return (
                f"Слишком длинный текст: {len(text)} символов, "
                f"максимум {broadcasts_service.TEXT_MAX}.",
                kb.cancel(),
            )
        await users_service.set_state(
            session, user, ST_BC_AUDIENCE, {"text": text, "photo": photo, "audio": audio}
        )
        return "Шаг 2 из 3.\nКому отправить оповещение?", kb.broadcast_audience()

    async def _broadcast_audience(
        self, session: AsyncSession, user: User, value: str
    ) -> Screen | str:
        """value: all | students | teachers | moderators | class:<id> | user:<id> —
        выбор сделан; cls, pick, pc:<класс>:<стр.>, back — промежуточные экраны."""
        if user.state != ST_BC_AUDIENCE:
            return "Оповещение уже отправлено или отменено."
        if value == "back":
            return "Шаг 2 из 3.\nКому отправить оповещение?", kb.broadcast_audience()
        if value in ("cls", "pick"):
            classes = await classes_service.all_classes(session)
            if value == "cls":
                return "Шаг 2 из 3.\nУченикам какого класса?", kb.broadcast_classes(classes, "bc:aud:class")
            return "Шаг 2 из 3.\nВ каком классе ученик?", kb.broadcast_classes(classes, "bc:aud:pc")
        if value.startswith("pc:"):
            return await self._broadcast_pick_student(session, value)
        if value.startswith("pr:"):
            return await self._broadcast_pick_person(session, value)
        try:
            title, _ = await broadcasts_service.check_audience(session, value)
        except BroadcastError as exc:
            return str(exc)
        count = len(await broadcasts_service.recipients(session, value, user))
        if not count:
            return "Среди этих получателей пока нет зарегистрированных пользователей."
        data = dict(user.state_data or {}, audience=value)
        await users_service.set_state(session, user, ST_BC_CONFIRM, data)
        to = await broadcasts_service.reader_label(session, value)
        text = (
            "Шаг 3 из 3. Проверьте оповещение — так его увидят получатели:\n"
            "――――――――――――\n"
            f"{self._announcement(data.get('text', ''), user, to)}\n"
            "――――――――――――\n"
            f"Получатели: {title}, {count} чел."
        )
        photo = _broadcast_media(data.get("photo"), data.get("audio"))
        if user.role == Role.moderator:
            text += "\nОтправится после подтверждения администратора."
        return text, photo + kb.broadcast_confirm(draft=user.role == Role.moderator)

    async def _broadcast_pick_student(self, session: AsyncSession, value: str) -> Screen | str:
        try:
            _, class_id, page = value.split(":")
            class_id, page = int(class_id), max(int(page), 0)
        except ValueError:
            class_id, page = _parse_id(value) or 0, 0
        try:
            school_class = await broadcasts_service.check_class(session, class_id)
        except BroadcastError as exc:
            return str(exc)
        students = await broadcasts_service.class_students(session, class_id)
        if not students:
            return f"В классе «{school_class.name}» пока нет зарегистрированных учеников."
        size = kb.STUDENT_PAGE
        shown = students[page * size : (page + 1) * size]
        return (
            f"Шаг 2 из 3.\nКому из класса «{school_class.name}» отправить оповещение?",
            kb.broadcast_people(
                shown, page, len(students) > (page + 1) * size,
                f"bc:aud:pc:{class_id}", ("« К классам", "bc:aud:pick"),
            ),
        )

    async def _broadcast_pick_person(self, session: AsyncSession, value: str) -> Screen | str:
        """Выбор одного учителя или модератора: value = pr:<роль>:<страница>."""
        try:
            _, role_name, page = value.split(":")
            role, page = Role(role_name), max(int(page), 0)
        except ValueError:
            return "Неизвестное действие."
        if role not in (Role.teacher, Role.moderator):
            return "Неизвестное действие."
        people = await broadcasts_service.people(session, role)
        who = "учителей" if role == Role.teacher else "модераторов"
        if not people:
            return f"Зарегистрированных {who} пока нет."
        size = kb.STUDENT_PAGE
        shown = people[page * size : (page + 1) * size]
        title = "Какому учителю" if role == Role.teacher else "Какому модератору"
        return (
            f"Шаг 2 из 3.\n{title} отправить оповещение?",
            kb.broadcast_people(
                shown, page, len(people) > (page + 1) * size,
                f"bc:aud:pr:{role.value}", ("« Назад", "bc:aud:back"),
            ),
        )

    async def _broadcast_send(self, session: AsyncSession, user: User) -> Screen | str:
        if user.state != ST_BC_CONFIRM:
            return "Оповещение уже отправлено или отменено."
        data = user.state_data or {}
        audience = data.get("audience") or broadcasts_service.legacy_audience(data.get("class_id"))
        await users_service.set_state(session, user, None)
        if user.role == Role.moderator:
            return await self._draft_submit(session, user, dict(data, audience=audience))
        try:
            await broadcasts_service.check_audience(session, audience)
            targets = await broadcasts_service.recipients(session, audience, user)
            broadcast = await broadcasts_service.create(
                session, user, data.get("text", ""), data.get("photo"), audience, targets,
                audio=data.get("audio"),
            )
        except BroadcastError as exc:
            return str(exc)
        photo = _broadcast_media(broadcast.photo, broadcast.audio)
        await self._run_later(
            self._broadcast(
                [t.max_user_id for t in targets],
                await self._broadcast_text(session, broadcast),
                photo + kb.broadcast_read(broadcast.id),
            )
        )
        return f"📤 Оповещение отправляется. Получателей: {len(targets)}.", kb.to_broadcasts()

    async def _draft_submit(self, session: AsyncSession, user: User, data: dict) -> Screen | str:
        """Модератор: оповещение уходит админам на подтверждение."""
        try:
            draft = await broadcasts_service.create_draft(
                session, user, data.get("text", ""), data.get("photo"), data["audience"],
                audio=data.get("audio"),
            )
        except BroadcastError as exc:
            return str(exc)
        target = await broadcasts_service.describe(session, draft.audience, draft.class_id)
        audit.record(user, "broadcast.draft", f"№{draft.id}, {target}: {draft.text[:200]}")
        photo = _broadcast_media(draft.photo, draft.audio)
        text = (
            f"📣 Модератор {user.display_name} подготовил(а) оповещение ({target}):\n"
            "――――――――――――\n"
            f"{self._announcement(draft.text, user, await broadcasts_service.reader_label(session, draft.audience, draft.class_id))}\n"
            "――――――――――――\n"
            "Без вашего подтверждения оно не уйдёт."
        )
        for admin in await users_service.admins(session):
            try:
                await self._send(admin.max_user_id, text, photo + kb.draft_actions(draft.id))
            except httpx.HTTPError:
                log.warning("Не удалось отправить оповещение на подтверждение %s", admin.max_user_id)
        return (
            "📨 Оповещение отправлено администратору на подтверждение. "
            "Когда он решит, вам придёт сообщение.",
            kb.to_broadcasts(),
        )

    async def _draft_decision(self, session: AsyncSession, user: User, payload: str) -> Screen | str:
        """Админ: отправить или отклонить оповещение модератора."""
        try:
            draft = await broadcasts_service.pending_draft(session, user, _parse_id(payload) or 0)
        except BroadcastError as exc:
            return str(exc)
        author = draft.author
        if payload.startswith("bcd:no:"):
            await broadcasts_service.close_draft(session, draft, user, DraftStatus.rejected)
            audit.record(user, "broadcast.reject", f"№{draft.id} от {author.display_name}")
            await self._notify(
                [author], "❌ Администратор не стал отправлять ваше оповещение.", kb.to_broadcasts()
            )
            return f"❌ Оповещение модератора {author.display_name} отклонено.", kb.back_to_menu()
        audience = draft.audience or broadcasts_service.legacy_audience(draft.class_id)
        try:
            targets = await broadcasts_service.recipients(session, audience, author)
            broadcast = await broadcasts_service.create(
                session, author, draft.text, draft.photo, audience, targets, approved_by=user,
                audio=draft.audio,
            )
        except BroadcastError as exc:
            return str(exc)
        await broadcasts_service.close_draft(session, draft, user, DraftStatus.sent, broadcast)
        audit.record(
            user, "broadcast.approve",
            f"№{draft.id} от {author.display_name}, получателей {len(targets)}",
        )
        photo = _broadcast_media(draft.photo, draft.audio)
        await self._run_later(
            self._broadcast(
                [t.max_user_id for t in targets],
                await self._broadcast_text(session, broadcast),
                photo + kb.broadcast_read(broadcast.id),
            )
        )
        await self._notify(
            [author],
            f"✅ Администратор подтвердил ваше оповещение. Получателей: {len(targets)}.",
            kb.to_broadcasts(),
        )
        return (
            f"📤 Оповещение модератора отправляется. Получателей: {len(targets)}.",
            kb.back_to_menu(),
        )

    async def _broadcast_read(
        self, session: AsyncSession, user: User, broadcast_id: int
    ) -> Screen | str:
        """Убирает кнопку «Прочитано» (оповещение остаётся) и присылает меню."""
        broadcast, reward = await broadcasts_service.mark_read(session, user, broadcast_id)
        note = f"💰 +{coins(reward)} за прочтение оповещения." if reward else ""
        await self._send(user.max_user_id, *self._menu(user, note))
        if reward:
            await self._check_achievements(session, user)
        if broadcast is None:
            return "Готово"
        photo = _broadcast_media(broadcast.photo, broadcast.audio)
        return await self._broadcast_text(session, broadcast), photo

    async def _inbox(self, session: AsyncSession, user: User, page: int) -> Screen:
        items, has_more = await broadcasts_service.inbox(
            session, user, page * kb.INBOX_PAGE, kb.INBOX_PAGE
        )
        if not items and page == 0:
            return "📣 Оповещений пока не было.", kb.back_to_menu()
        title = "📣 Полученные оповещения, новые сверху"
        unread = await broadcasts_service.unread_count(session, user)
        if unread:
            title += f"\n🔵 Непрочитанных: {unread}"
        if page:
            title += f"\nСтраница {page + 1}"
        return title, kb.inbox_list(items, page, has_more, _format_day)

    async def _inbox_open(
        self, session: AsyncSession, user: User, broadcast_id: int
    ) -> str:
        broadcast, reward = await broadcasts_service.mark_read(session, user, broadcast_id)
        if broadcast is None:
            return "Оповещение не найдено."
        text = (
            f"{await self._broadcast_text(session, broadcast)}\n\n"
            f"Получено: {_format_date(broadcast.created_at)}"
        )
        if reward:
            text += f"\n💰 +{coins(reward)} за прочтение. Баланс: {coins(user.balance)}"
        photo = _broadcast_media(broadcast.photo, broadcast.audio)
        await self._send(user.max_user_id, text, photo + kb.to_inbox())
        if reward:
            await self._check_achievements(session, user)
        return "Оповещение открыто ниже"

    async def _broadcast_history(self, session: AsyncSession, page: int) -> Screen:
        items, has_more = await broadcasts_service.history(
            session, page * kb.BROADCAST_PAGE, kb.BROADCAST_PAGE
        )
        if not items:
            return "🗂 Отправленных оповещений пока нет.", kb.broadcast_history([], 0, False)
        title = "🗂 Отправленные оповещения, новые сверху"
        if page:
            title += f"\nСтраница {page + 1}"
        return title, kb.broadcast_history(items, page, has_more)

    async def _broadcast_open(self, session: AsyncSession, user: User, broadcast_id: int) -> str:
        broadcast = await session.get(broadcasts_service.Broadcast, broadcast_id)
        if broadcast is None:
            return "Оповещение не найдено."
        target = await broadcasts_service.describe(session, broadcast.audience, broadcast.class_id)
        text = (
            f"{await self._broadcast_text(session, broadcast)}\n\n"
            "――――――――――――\n"
            f"Получатели: {target}, {broadcast.recipients} чел.\n"
            f"Автор: {broadcast.author.display_name}\n"
            f"Отправлено: {_format_date(broadcast.created_at)}"
        )
        photo = _broadcast_media(broadcast.photo, broadcast.audio)
        await self._send(user.max_user_id, text, photo + kb.to_broadcasts())
        return "Оповещение открыто ниже"

    # --- монеты: ручное начисление и списание ---

    async def _coins(self, session: AsyncSession, user: User, payload: str) -> Screen | str:
        if not coins_service.can_use(user):
            return "Раздел доступен учителям и админам."
        if payload == "menu:coins":
            if user.role == Role.admin:
                return await self._coins_classes(session)
            classes = user.teacher_classes
            if not classes:
                return "Вам ещё не назначены классы. Обратитесь к администратору."
            if len(classes) > 1:
                return "💰 Монеты\nВыберите класс:", kb.coins_classes(classes)
            return await self._coins_students(session, user, classes[0].id, 0)
        if payload.startswith("coins:cls:"):
            # coins:cls:<класс>:<страница>
            try:
                _, _, class_id, page = payload.split(":")
                class_id, page = int(class_id), max(int(page), 0)
            except ValueError:
                return "Неизвестное действие."
            if user.role != Role.admin and class_id not in user.teacher_class_ids:
                return "Доступны только ваши классы."
            return await self._coins_students(session, user, class_id, page)
        if payload == "coins:ok":
            return await self._coins_apply(session, user)
        if payload.startswith("coins:all:"):
            return await self._coins_class_start(session, user, _parse_id(payload) or 0)

        try:
            student = await coins_service.get_student(session, user, _parse_id(payload) or 0)
        except CoinsError as exc:
            return str(exc)
        if payload.startswith("coins:st:"):
            return await self._coins_card(session, user, student)
        if payload.startswith(("coins:give:", "coins:take:")):
            sign = 1 if payload.startswith("coins:give:") else -1
            remaining = await coins_service.remaining_today(session, user)
            if remaining == 0:
                return "Дневной лимит исчерпан, продолжить можно завтра."
            limit = coins_service.per_operation_limit(user)
            if remaining is not None:
                limit = min(limit, remaining)
            if sign < 0:
                if student.balance == 0:
                    return "У ученика нет монет."
                limit = min(limit, student.balance)
            await users_service.set_state(
                session, user, ST_COINS_AMOUNT, {"student_id": student.id, "sign": sign}
            )
            verb = "начислить" if sign > 0 else "списать"
            return (
                f"{'➕' if sign > 0 else '➖'} Сколько монет {verb}: {student.display_name}?\n"
                f"Число от 1 до {limit}. Сейчас у ученика {coins(student.balance)}.",
                kb.cancel(),
            )
        return "Неизвестное действие."

    async def _coins_classes(self, session: AsyncSession) -> Screen:
        classes = await classes_service.all_classes(session)
        if not classes:
            return "💰 Классов пока нет.", kb.back_to_menu()
        return "💰 Монеты\nВыберите класс:", kb.coins_classes(classes)

    async def _coins_students(
        self, session: AsyncSession, user: User, class_id: int, page: int
    ) -> Screen | str:
        school_class = await session.get(SchoolClass, class_id)
        if school_class is None:
            return "Класс не найден."
        students = await classes_service.students(session, class_id)
        start = page * kb.COINS_PAGE
        items = students[start : start + kb.COINS_PAGE]
        lines = [f"💰 Монеты · класс «{school_class.name}»"]
        lines.append("Выберите ученика:" if students else "В классе пока нет учеников.")
        remaining = await coins_service.remaining_today(session, user)
        if remaining is not None:
            lines.append(self._teacher_limits(remaining))
        return "\n".join(lines), kb.coins_students(
            items,
            class_id,
            page,
            len(students) > start + kb.COINS_PAGE,
            user.role == Role.admin or len(user.teacher_classes) > 1,
        )

    # --- начисление всему классу ---

    async def _coins_class_start(
        self, session: AsyncSession, user: User, class_id: int
    ) -> Screen | str:
        if user.role != Role.admin and class_id not in user.teacher_class_ids:
            return "Доступны только ваши классы."
        school_class = await session.get(SchoolClass, class_id)
        if school_class is None:
            return "Класс не найден."
        students = await classes_service.students(session, class_id)
        if not students:
            return "В классе нет учеников."
        await users_service.set_state(session, user, ST_COINS_AMOUNT, {"class_id": class_id})
        lines = [
            f"👥 Начислить всему классу «{school_class.name}» ({len(students)} уч.).",
            "Сколько монет каждому?",
        ]
        remaining = await coins_service.remaining_today(session, user)
        if remaining is not None:
            lines.append(
                f"Сегодня можно ещё {remaining} монет всего — "
                f"это до {min(remaining // len(students), coins_service.TEACHER_PER_OPERATION)} "
                "каждому."
            )
        return "\n".join(lines), kb.cancel()

    async def _coins_class_step(
        self, session: AsyncSession, user: User, text: str, data: dict
    ) -> Screen:
        class_id = data["class_id"]
        try:
            if user.state == ST_COINS_AMOUNT:
                amount = coins_service.parse_amount(text)
                await coins_service.check_class_amount(session, user, class_id, amount)
                await users_service.set_state(
                    session, user, ST_COINS_REASON, dict(data, amount=amount)
                )
                return "За что? Коротко напишите причину, её увидит каждый ученик.", kb.cancel()
            # ST_COINS_REASON
            if len(text) > coins_service.REASON_MAX:
                raise CoinsError(f"Слишком длинно, максимум {coins_service.REASON_MAX} символов.")
            amount = data.get("amount", 0)
            students = await coins_service.check_class_amount(session, user, class_id, amount)
            school_class = await session.get(SchoolClass, class_id)
            await users_service.set_state(session, user, ST_COINS_CONFIRM, dict(data, reason=text))
            return (
                f"Начислить по {coins(amount)} каждому из {len(students)} учеников "
                f"класса «{school_class.name}»?\n"
                f"Всего: {coins(amount * len(students))}\nПричина: {text}",
                kb.coins_confirm(),
            )
        except CoinsError as exc:
            return str(exc), kb.cancel()

    async def _coins_class_apply(
        self, session: AsyncSession, user: User, data: dict
    ) -> Screen | str:
        class_id, amount, reason = data["class_id"], data.get("amount", 0), data.get("reason", "")
        try:
            students = await coins_service.apply_class(session, user, class_id, amount, reason)
        except CoinsError as exc:
            return str(exc)
        for student in students:
            await self._check_achievements(session, student)
        messages = [
            (
                s.max_user_id,
                f"💰 Вам и всему классу начислено по {coins(amount)}\n"
                f"Причина: {reason.strip()}\nБаланс: {coins(s.balance)}",
            )
            for s in students
        ]
        await self._run_later(self._send_each(messages, kb.back_to_menu()))
        screen = await self._coins_students(session, user, class_id, 0)
        if isinstance(screen, str):
            return screen
        note = (
            f"✅ Начислено по {coins(amount)} каждому из {len(students)} учеников, "
            f"всего {coins(amount * len(students))}.\n\n"
        )
        return note + screen[0], screen[1]

    async def _send_each(self, messages: list[tuple[int, str]], attachments: list[dict]) -> None:
        """Персональные сообщения по очереди, с паузой как у рассылок."""
        for i, (user_id, text) in enumerate(messages):
            if i:
                await asyncio.sleep(BROADCAST_INTERVAL)
            try:
                await self._send(user_id, text, attachments)
            except httpx.HTTPError:
                log.warning("Не удалось уведомить ученика %s", user_id)

    @staticmethod
    def _teacher_limits(remaining: int) -> str:
        return (
            f"Ваш лимит: не больше {coins_service.TEACHER_PER_OPERATION} за раз, "
            f"сегодня осталось {remaining} из {coins_service.TEACHER_DAILY}."
        )

    async def _coins_card(
        self, session: AsyncSession, user: User, student: User, note: str = ""
    ) -> Screen:
        lines = [note, ""] if note else []
        lines.append(f"💰 {student.display_name}")
        if student.school_class:
            lines.append(f"Класс: {student.school_class.name}")
        lines.append(f"Баланс: {coins(student.balance)}")
        operations = await coins_service.history(session, student)
        if operations:
            lines += ["", "Последние операции:"]
            for t in operations:
                lines.append(
                    f"{t.amount:+} · {_format_day(t.created_at)} · {t.reason} "
                    f"({t.actor.display_name})"
                )
        remaining = await coins_service.remaining_today(session, user)
        if remaining is not None:
            lines += ["", self._teacher_limits(remaining)]
        return "\n".join(lines), kb.coins_card(student)

    async def _coins_step(self, session: AsyncSession, user: User, text: str) -> Screen:
        data = dict(user.state_data or {})
        if user.state == ST_COINS_CONFIRM:
            return "Нажмите «Подтвердить» или «Отмена».", kb.coins_confirm()
        if not text or text.startswith("/"):
            return "Пришлите ответ текстом или нажмите «Отмена».", kb.cancel()
        if "class_id" in data:
            return await self._coins_class_step(session, user, text, data)
        try:
            student = await coins_service.get_student(session, user, data.get("student_id", 0))
            if user.state == ST_COINS_AMOUNT:
                amount = coins_service.parse_amount(text) * data.get("sign", 1)
                await coins_service.check_amount(session, user, student, amount)
                await users_service.set_state(
                    session, user, ST_COINS_REASON, dict(data, amount=amount)
                )
                return (
                    "За что? Коротко напишите причину, её увидит ученик.",
                    kb.cancel(),
                )
            # ST_COINS_REASON
            if len(text) > coins_service.REASON_MAX:
                raise CoinsError(f"Слишком длинно, максимум {coins_service.REASON_MAX} символов.")
            amount = data.get("amount", 0)
            await users_service.set_state(session, user, ST_COINS_CONFIRM, dict(data, reason=text))
            verb = "Начислить" if amount > 0 else "Списать"
            return (
                f"{verb} {coins(abs(amount))}: {student.display_name}?\n"
                f"Причина: {text}\n"
                f"Баланс станет: {coins(student.balance + amount)}",
                kb.coins_confirm(),
            )
        except CoinsError as exc:
            return str(exc), kb.cancel()

    async def _coins_apply(self, session: AsyncSession, user: User) -> Screen | str:
        if user.state != ST_COINS_CONFIRM:
            return "Операция уже выполнена или отменена."
        data = user.state_data or {}
        await users_service.set_state(session, user, None)
        if "class_id" in data:
            return await self._coins_class_apply(session, user, data)
        try:
            transaction = await coins_service.apply(
                session, user, data.get("student_id", 0), data.get("amount", 0), data.get("reason", "")
            )
        except CoinsError as exc:
            return str(exc)
        student = await session.get(User, transaction.user_id)
        amount = transaction.amount
        if amount > 0:
            note = f"✅ Начислено {coins(amount)}."
            message = f"💰 Вам начислено {coins(amount)}"
        else:
            note = f"✅ Списано {coins(-amount)}."
            message = f"💸 Списано {coins(-amount)}"
        message += f"\nПричина: {transaction.reason}\nБаланс: {coins(student.balance)}"
        try:
            await self._send(student.max_user_id, message, kb.back_to_menu())
        except httpx.HTTPError:
            log.warning("Не удалось уведомить ученика %s", student.max_user_id)
        if amount > 0:
            await self._check_achievements(session, student)
        return await self._coins_card(session, user, student, note)

    # --- награды (админ) ---

    async def _rewards(self, session: AsyncSession, user: User, payload: str) -> Screen | str:
        if user.role == Role.moderator:
            return await self._rewards_view(session, payload)
        if not shop_service.can_manage(user):
            return "Раздел доступен только админам."
        if payload == "menu:rewards" or payload.startswith("rw:list:"):
            return await self._reward_list(session, max(_parse_id(payload) or 0, 0))
        if payload == "rw:new":
            await users_service.set_state(session, user, ST_RW_TITLE, {})
            return "🎁 Новая награда. Шаг 1 из 5.\nНапишите название награды.", kb.cancel()
        if payload == "rw:stock:inf":
            if user.state == ST_RW_STOCK:
                data = dict(user.state_data or {}, stock=None)
                await users_service.set_state(session, user, ST_RW_PHOTO, data)
                return self._reward_photo_prompt()
            if user.state == ST_RW_EDIT_STOCK:
                reward_id = (user.state_data or {}).get("reward_id", 0)
                await users_service.set_state(session, user, None)
                return await self._reward_update(
                    session, user, reward_id, "📦 Теперь без ограничения.", unlimited=True
                )
            return "Действие уже завершено или отменено."
        if payload == "rw:photo:none":
            if user.state != ST_RW_PHOTO:
                return "Действие уже завершено или отменено."
            return await self._reward_create(session, user, None)

        reward_id = _parse_id(payload) or 0
        try:
            reward = await shop_service.get_managed(session, user, reward_id)
        except ShopError as exc:
            return str(exc)
        if payload.startswith("rw:open:"):
            return await self._reward_card(session, reward)
        if payload.startswith("rw:price:"):
            await users_service.set_state(session, user, ST_RW_EDIT_PRICE, {"reward_id": reward.id})
            return (
                f"Новая цена для «{reward.title}» (сейчас {coins(reward.price)}).\n"
                f"Число от 1 до {shop_service.PRICE_MAX}.",
                kb.cancel(),
            )
        if payload.startswith("rw:qty:"):
            await users_service.set_state(session, user, ST_RW_EDIT_STOCK, {"reward_id": reward.id})
            now = "без ограничения" if reward.stock is None else f"{reward.stock} шт."
            return (
                f"Сколько штук «{reward.title}» доступно? Сейчас: {now}.\n"
                "Напишите число или нажмите «Без ограничения».",
                kb.reward_stock_choice(),
            )
        if payload.startswith(("rw:hide:", "rw:show:")):
            show = payload.startswith("rw:show:")
            note = "👁 Награда снова в магазине." if show else "🙈 Награда скрыта из магазина."
            return await self._reward_update(session, user, reward.id, note, active=show)
        return "Неизвестное действие."

    async def _rewards_view(self, session: AsyncSession, payload: str) -> Screen | str:
        """Модератор: награды только для просмотра, кнопки управления — с замком."""
        if payload == "menu:rewards" or payload.startswith("rw:list:"):
            return await self._reward_list(
                session, max(_parse_id(payload) or 0, 0), readonly=True
            )
        if payload.startswith("rw:open:"):
            reward = await session.get(Reward, _parse_id(payload) or 0)
            if reward is None:
                return "Награда не найдена."
            return await self._reward_card(session, reward, readonly=True)
        return "🔒 Недоступно модератору: это делает админ."

    async def _reward_list(self, session: AsyncSession, page: int, readonly: bool = False) -> Screen:
        items, has_more = await shop_service.all_rewards(
            session, page * kb.REWARD_PAGE, kb.REWARD_PAGE
        )
        text = "🎁 Награды магазина"
        if items and readonly:
            text += "\nТолько просмотр. 🙈 — скрыта из магазина."
        elif items:
            text += "\nВыберите награду или добавьте новую. 🙈 — скрыта из магазина."
        else:
            text += "\nНаград пока нет. Добавьте первую."
        if page:
            text += f"\nСтраница {page + 1}"
        return text, kb.reward_admin_list(
            items, page, has_more, readonly, pending=self.menu_counts.get("menu:rewards", 0)
        )

    async def _reward_card(
        self, session: AsyncSession, reward, note: str = "", readonly: bool = False
    ) -> Screen:
        stats = await shop_service.reward_stats(session, reward.id)
        pending = stats.get(OrderStatus.pending, 0)
        sold = pending + stats.get(OrderStatus.issued, 0)
        lines = [note, ""] if note else []
        lines.append("🟢 В магазине" if reward.is_active else "🙈 Скрыта из магазина")
        lines.append(f"🎁 {reward.title}")
        if reward.description:
            lines += ["", reward.description]
        lines += [
            "",
            f"Цена: {coins(reward.price)}",
            "В наличии: " + ("без ограничения" if reward.stock is None else f"{reward.stock} шт."),
            f"Куплено: {sold}" + (f" (ждут выдачи: {pending})" if pending else ""),
        ]
        if reward.kind == RewardKind.lottery:
            wins, paid = await shop_service.lottery_wins(session, reward.id)
            lines += [
                f"🎡 Колесо фортуны: шанс {reward.win_chance}%, выигрыш {coins(reward.prize or 0)}",
                f"Выигрышей: {wins}, выплачено {coins(paid)}",
            ]
            if reward.loss_chance:
                losses, taken = await shop_service.lottery_losses(session, reward.id)
                lines += [
                    f"💥 Критический провал: шанс {reward.loss_chance}%, "
                    f"потеря до {coins(reward.loss or 0)}",
                    f"Провалов: {losses}, списано {coins(taken)}",
                ]
        photo = [_image_attachment(reward.photo)] if reward.photo else []
        return "\n".join(lines), photo + kb.reward_admin_card(reward, readonly)

    async def _reward_update(
        self, session: AsyncSession, user: User, reward_id: int, note: str, **changes
    ) -> Screen | str:
        try:
            reward = await shop_service.update_reward(session, user, reward_id, **changes)
        except ShopError as exc:
            return str(exc)
        return await self._reward_card(session, reward, note)

    @staticmethod
    def _reward_photo_prompt() -> Screen:
        return (
            "Шаг 5 из 5.\nПришлите фото награды или нажмите «Без фото».",
            kb.reward_photo_choice(),
        )

    async def _reward_step(
        self, session: AsyncSession, user: User, text: str, attachments: list[dict]
    ) -> Screen:
        data = dict(user.state_data or {})
        if user.state == ST_RW_PHOTO:
            photo = _find_image(attachments)
            if photo is None:
                return self._reward_photo_prompt()
            return await self._reward_create(session, user, photo)
        if not text or text.startswith("/"):
            return "Пришлите ответ текстом или нажмите «Отмена».", kb.cancel()
        try:
            if user.state == ST_RW_TITLE:
                if len(text) > shop_service.TITLE_MAX:
                    raise ShopError(f"Слишком длинное название, максимум {shop_service.TITLE_MAX}.")
                await users_service.set_state(session, user, ST_RW_DESC, dict(data, title=text))
                return (
                    "Шаг 2 из 5.\nОпишите награду: что получит ученик и как её выдают.",
                    kb.cancel(),
                )
            if user.state == ST_RW_DESC:
                if len(text) > shop_service.DESCRIPTION_MAX:
                    raise ShopError(
                        f"Слишком длинное описание, максимум {shop_service.DESCRIPTION_MAX}."
                    )
                await users_service.set_state(
                    session, user, ST_RW_PRICE, dict(data, description=text)
                )
                return (
                    f"Шаг 3 из 5.\nЦена в монетах: число от 1 до {shop_service.PRICE_MAX}.",
                    kb.cancel(),
                )
            if user.state == ST_RW_PRICE:
                price = shop_service.parse_price(text)
                await users_service.set_state(session, user, ST_RW_STOCK, dict(data, price=price))
                return (
                    "Шаг 4 из 5.\nСколько штук доступно? Напишите число "
                    "или нажмите «Без ограничения».",
                    kb.reward_stock_choice(),
                )
            if user.state == ST_RW_STOCK:
                stock = shop_service.parse_stock(text)
                if stock == 0:
                    raise ShopError("Для новой награды нужна хотя бы 1 штука.")
                await users_service.set_state(session, user, ST_RW_PHOTO, dict(data, stock=stock))
                return self._reward_photo_prompt()
            if user.state == ST_RW_EDIT_PRICE:
                price = shop_service.parse_price(text)
                await users_service.set_state(session, user, None)
                return await self._reward_update(
                    session, user, data.get("reward_id", 0), "💰 Цена изменена.", price=price
                )
            # ST_RW_EDIT_STOCK
            stock = shop_service.parse_stock(text)
            await users_service.set_state(session, user, None)
            return await self._reward_update(
                session, user, data.get("reward_id", 0), "📦 Количество изменено.", stock=stock
            )
        except ShopError as exc:
            keyboard = (
                kb.reward_stock_choice()
                if user.state in (ST_RW_STOCK, ST_RW_EDIT_STOCK)
                else kb.cancel()
            )
            return str(exc), keyboard

    async def _reward_create(self, session: AsyncSession, user: User, photo: dict | None) -> Screen:
        data = user.state_data or {}
        await users_service.set_state(session, user, None)
        try:
            reward = await shop_service.create_reward(
                session,
                user,
                data["title"],
                data.get("description", ""),
                data["price"],
                data.get("stock"),
                photo,
            )
        except (ShopError, KeyError) as exc:
            text = str(exc) if isinstance(exc, ShopError) else "Не удалось создать награду."
            return text, kb.to_rewards()
        return await self._reward_card(session, reward, "🎉 Награда добавлена в магазин.")

    # --- магазин (ученик) ---

    async def _shop(self, session: AsyncSession, user: User, payload: str) -> Screen | str:
        if payload == "menu:shop":
            return await self._shop_list(session, user)
        if payload.startswith("shop:mine:"):
            return await self._my_orders(session, user, max(_parse_id(payload) or 0, 0))
        reward_id = _parse_id(payload) or 0
        try:
            if payload.startswith("shop:open:"):
                return await self._shop_card(session, user, reward_id)
            if payload.startswith("shop:buy:"):
                reward = await shop_service.get_for_sale(session, reward_id)
                if user.balance < reward.price:
                    raise ShopError(
                        f"Не хватает монет: нужно {reward.price}, у вас {user.balance}."
                    )
                left = coins(user.balance - reward.price)
                if reward.kind == RewardKind.lottery:
                    risk = (
                        f"С шансом {reward.loss_chance}% — критический провал: колесо заберёт "
                        f"{coins(reward.loss or 0)} (баланс не уйдёт в минус).\n"
                        if reward.loss_chance
                        else ""
                    )
                    return (
                        f"🎡 Крутить «{reward.title}» за {coins(reward.price)}?\n"
                        f"С шансом {reward.win_chance}% выиграете {coins(reward.prize or 0)}.\n"
                        f"{risk}После вращения останется {left} (если не повезёт).",
                        kb.shop_confirm(reward.id),
                    )
                return (
                    f"Купить «{reward.title}» за {coins(reward.price)}?\n"
                    f"После покупки останется {left}.",
                    kb.shop_confirm(reward.id),
                )
            if payload.startswith("shop:buy_ok:"):
                order = await shop_service.buy(session, user, reward_id)
                await self._check_achievements(session, user)
                if order.reward.kind == RewardKind.lottery:
                    return await self._spin_result(session, user, order)
                await self._notify_handlers(session, order)
                return (
                    f"🎉 Покупка оформлена! Заказ №{order.id}: «{order.reward.title}».\n"
                    "Награду выдаст учитель или админ, вам придёт сообщение.\n"
                    f"Баланс: {coins(user.balance)}",
                    kb.after_purchase(),
                )
        except ShopError as exc:
            return str(exc)
        return "Неизвестное действие."

    async def _spin_result(self, session: AsyncSession, user: User, order) -> Screen:
        reward = order.reward
        notice = None
        if order.prize_won and order.prize_won > 0:
            text = (
                f"🎡 Колесо крутится… и…\n\n🎉 Критический успех! "
                f"Вы выиграли {coins(order.prize_won)}!\nБаланс: {coins(user.balance)}"
            )
            notice = (
                f"🎡 {user.display_name} выиграл(а) {coins(order.prize_won)} "
                f"в «{reward.title}» (вращение №{order.id})"
            )
        elif order.prize_won and order.prize_won < 0:
            text = (
                f"🎡 Колесо крутится… и…\n\n💥 Критический провал! "
                f"Колесо забрало {coins(-order.prize_won)}.\nБаланс: {coins(user.balance)}"
            )
            notice = (
                f"💥 {user.display_name}: критический провал в «{reward.title}», "
                f"списано {coins(-order.prize_won)} (вращение №{order.id})"
            )
        else:
            text = (
                f"🎡 Колесо крутится… и…\n\n😔 В этот раз не повезло. Попробуйте ещё!\n"
                f"Баланс: {coins(user.balance)}"
            )
        if notice:
            for admin in await shop_service.admins(session):
                try:
                    await self._send(admin.max_user_id, notice)
                except httpx.HTTPError:
                    log.warning("Не удалось уведомить админа %s о вращении", admin.max_user_id)
        can_repeat = reward.is_active and user.balance >= reward.price and (
            reward.stock is None or reward.stock > 0
        )
        return text, kb.after_spin(reward.id, can_repeat)

    async def _shop_list(self, session: AsyncSession, user: User) -> Screen:
        items = await shop_service.shop_items(session)
        if not items:
            return "🛒 В магазине пока пусто. Загляните позже!", kb.shop_list([], user.balance)
        text = f"🛒 Магазин наград\nБаланс: {coins(user.balance)}"
        if any(r.price > user.balance for r in items):
            text += "\n🔒 — пока не хватает монет"
        return text, kb.shop_list(items, user.balance)

    async def _shop_card(self, session: AsyncSession, user: User, reward_id: int) -> Screen:
        reward = await shop_service.get_for_sale(session, reward_id)
        lines = [f"🎁 {reward.title}"]
        if reward.description:
            lines += ["", reward.description]
        lines += ["", f"Цена: {coins(reward.price)}"]
        if reward.kind == RewardKind.lottery:
            lines.append(
                f"🎡 Шанс {reward.win_chance}% выиграть {coins(reward.prize or 0)}. "
                "Крутить можно сколько угодно раз."
            )
            if reward.loss_chance:
                lines.append(
                    f"💥 Но с шансом {reward.loss_chance}% — критический провал: "
                    f"колесо заберёт {coins(reward.loss or 0)} (баланс не уйдёт в минус)."
                )
        if reward.stock is not None:
            lines.append(f"Осталось: {reward.stock} шт.")
        affordable = user.role == Role.student and user.balance >= reward.price
        if user.role == Role.student and not affordable:
            lines.append(f"Не хватает: {coins(reward.price - user.balance)}")
        photo = [_image_attachment(reward.photo)] if reward.photo else []
        return "\n".join(lines), photo + kb.shop_card(reward, affordable)

    async def _my_orders(self, session: AsyncSession, user: User, page: int) -> Screen:
        items, has_more = await shop_service.my_orders(
            session, user, page * kb.ORDERS_PAGE, kb.ORDERS_PAGE
        )
        if not items and page == 0:
            return "📦 Покупок пока нет.", kb.my_orders_nav(0, False)
        lines = ["📦 Мои покупки, новые сверху", ""]
        for o in items:
            if o.prize_won is not None:
                status = (
                    f"🎉 выигрыш {coins(o.prize_won)}" if o.prize_won > 0
                    else f"💥 провал −{coins(-o.prize_won)}" if o.prize_won < 0
                    else "🎡 без выигрыша"
                )
            else:
                status = kb.ORDER_STATUS_TITLES[o.status]
            lines.append(
                f"{status} · №{o.id} «{o.reward.title}», "
                f"{coins(o.price)} · {_format_day(o.created_at)}"
            )
        if page:
            lines.append(f"\nСтраница {page + 1}")
        return "\n".join(lines), kb.my_orders_nav(page, has_more)

    async def _notify_handlers(self, session: AsyncSession, order) -> None:
        student = order.user
        klass = f" ({student.school_class.name})" if student.school_class else ""
        text = (
            f"🛒 Новая покупка, заказ №{order.id}\n"
            f"{student.display_name}{klass} — «{order.reward.title}», {coins(order.price)}"
        )
        for handler in await shop_service.handlers_for(session, order):
            try:
                await self._send(handler.max_user_id, text, kb.order_notice(order.id))
            except httpx.HTTPError:
                log.warning("Не удалось уведомить о покупке %s", handler.max_user_id)

    # --- выдача наград (админ и учитель класса) ---

    async def _orders(self, session: AsyncSession, user: User, payload: str) -> Screen | str:
        if user.role not in (Role.teacher, Role.admin, Role.moderator):
            return "Раздел доступен учителям, модераторам и админам."
        if payload == "ord:list":
            items = await shop_service.pending_for(session, user)
            staff = user.role in (Role.admin, Role.moderator)
            if not items:
                return "📦 Наград на выдачу нет.", kb.no_orders(staff)
            return f"📦 Ждут выдачи: {len(items)}. Выберите заказ:", kb.orders_list(items, staff)
        order_id = _parse_id(payload) or 0
        try:
            if payload.startswith("ord:open:"):
                order = await shop_service.get_order(session, user, order_id)
                return self._order_card(order, viewer=user)
            if payload.startswith("ord:cancel:"):
                if not shop_service.can_cancel(user):
                    return "Отменять заказы могут учитель класса и админ."
                order = await shop_service.get_order(session, user, order_id)
                if order.status != OrderStatus.pending:
                    raise ShopError("Этот заказ уже обработан.")
                return (
                    f"Отменить заказ №{order.id} «{order.reward.title}»?\n"
                    f"{order.user.display_name} получит обратно {coins(order.price)}.",
                    kb.order_cancel_confirm(order.id),
                )
            if payload.startswith("ord:issue:"):
                order = await shop_service.issue(session, user, order_id)
                if user.role in (Role.moderator, Role.admin):
                    audit.record(
                        user, "order.issue",
                        f"№{order.id} «{order.reward.title}» — {order.user.display_name}",
                    )
                note = f"🎁 Награда «{order.reward.title}» выдана! Заказ №{order.id}."
                await self._notify_student(order, note)
                return self._order_card(order, "✅ Отмечено как выданное.", user)
            if payload.startswith("ord:cancel_ok:"):
                order = await shop_service.cancel(session, user, order_id)
                note = (
                    f"↩️ Заказ №{order.id} «{order.reward.title}» отменён, "
                    f"{coins(order.price)} возвращены.\nБаланс: {coins(order.user.balance)}"
                )
                await self._notify_student(order, note)
                if user.role == Role.admin:
                    audit.record(user, "order.cancel", f"№{order.id} «{order.reward.title}»")
                return self._order_card(order, "↩️ Заказ отменён, монеты возвращены.", user)
        except ShopError as exc:
            return str(exc)
        return "Неизвестное действие."

    def _order_card(self, order, note: str = "", viewer: User | None = None) -> Screen:
        student = order.user
        lines = [note, ""] if note else []
        lines += [
            f"Заказ №{order.id}",
            f"Ученик: {student.display_name}",
        ]
        if student.school_class:
            lines.append(f"Класс: {student.school_class.name}")
        lines += [
            f"Награда: «{order.reward.title}»",
            f"Цена: {coins(order.price)}",
            f"Куплено: {_format_date(order.created_at)}",
            f"Статус: {kb.ORDER_STATUS_TITLES[order.status]}",
        ]
        keyboard = (
            kb.order_actions(order.id, viewer is None or shop_service.can_cancel(viewer))
            if order.status == OrderStatus.pending
            else kb.to_orders()
        )
        return "\n".join(lines), keyboard

    async def _notify_student(self, order, text: str) -> None:
        try:
            await self._send(order.user.max_user_id, text, kb.after_purchase())
        except httpx.HTTPError:
            log.warning("Не удалось уведомить ученика %s", order.user.max_user_id)

    # --- создание квеста ---

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
        steps = 7 if Bot._needs_audience(user) else 6
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
        photo = None if skip else _find_image(attachments)
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

    # --- управление квестами ---

    async def _manage(self, session: AsyncSession, user: User, payload: str) -> Screen | str:
        if user.role not in (Role.teacher, Role.admin, Role.moderator):
            return "Раздел доступен учителям, модераторам и админам."
        _, action, *rest = payload.split(":")
        if action == "list":
            active = rest[0] == "a" if rest else True
            return await self._managed_list(session, user, active, _parse_id(payload) or 0)
        quest_id = _parse_id(payload) or 0
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

    # --- проверка заявок ---

    def _can_open_review(self, user: User) -> bool:
        if user.role == Role.moderator:
            return self.moderator_review
        return user.role in (Role.teacher, Role.admin)

    async def _review_list(self, session: AsyncSession, user: User) -> Screen | str:
        if not self._can_open_review(user):
            return "Раздел доступен учителям и админам."
        items = await quests_service.pending_for(session, user)
        if not items:
            return "📋 Заявок на проверку нет.", kb.review_empty()
        return f"📋 Ждут проверки: {len(items)}. Выберите заявку:", kb.review_list(items)

    async def _send_submission(
        self, user: User, submission: Submission, text: str, buttons: list[dict]
    ) -> None:
        """Новое сообщение с ответом; если вложения не отправились — со ссылками на них."""
        media = self._submission_media(submission)
        if not media:
            await self._send(user.max_user_id, text, buttons)
            return
        try:
            await self._send(user.max_user_id, text, media + buttons)
        except httpx.HTTPStatusError:
            log.warning("Не удалось показать вложения заявки %s", submission.id)
            links = []
            if submission.photo:
                links.append(f"Фото: {submission.photo.get('url', 'нет ссылки')}")
            if submission.video:
                links.append(f"Видео: {submission.video.get('url', 'нет ссылки')}")
            await self._send(
                user.max_user_id,
                f"{text}\n\nВложения не открылись в чате.\n" + "\n".join(links),
                buttons,
            )

    @staticmethod
    def _submission_media(submission: Submission) -> list[dict]:
        media = []
        if submission.photo:
            media.append(_image_attachment(submission.photo))
        if submission.video:
            media.append(_video_attachment(submission.video))
        return media

    @staticmethod
    def _submission_text(submission: Submission) -> str:
        text = (
            f"Заявка №{submission.id}\n"
            f"Ученик: {submission.user.display_name}\n"
            f"Квест: «{submission.quest.title}»\n"
            f"Награда: {kb.reward_text(submission.quest)}"
        )
        if submission.text:
            text += f"\n\n📝 Ответ:\n{submission.text}"
        if submission.video:
            text += "\n\n🎬 Ответ с видео"
        if submission.review_comment:
            text += f"\n\n💬 Причина отклонения: {submission.review_comment}"
        return text

    async def _review_open(
        self, session: AsyncSession, user: User, submission_id: int | None
    ) -> str:
        items = {s.id: s for s in await quests_service.pending_for(session, user)}
        submission = items.get(submission_id or 0)
        if submission is None:
            return "Заявка уже проверена или не найдена."
        await self._send_submission(
            user, submission, self._submission_text(submission), self._review_keyboard(submission)
        )
        return "Заявка открыта ниже"

    async def _archive_list(
        self, session: AsyncSession, user: User, page: int, daily: bool = False
    ) -> Screen | str:
        if not self._can_open_review(user):
            return "Раздел доступен учителям и админам."
        if daily and not await quests_service.sees_daily_archive(session, user):
            return "Раздел доступен только админу."
        page = max(page, 0)
        items, has_more = await quests_service.reviewed_for(
            session, user, page * kb.ARCHIVE_PAGE, kb.ARCHIVE_PAGE, daily=daily
        )
        button = None
        if not daily and await quests_service.sees_daily_archive(session, user):
            titles = await quests_service.daily_review_titles(session)
            if titles:
                button = "✅ Принятые: " + ", ".join(titles)
        if daily:
            text = "🗂 Принятые ответы ежедневных квестов, новые сверху"
        else:
            text = "🗂 Архив проверенных заявок, новые сверху.\n✅ — подтверждена, ❌ — отклонена"
            if button:
                text += "\nПринятые ответы ежедневных квестов — отдельной кнопкой ниже"
        if not items:
            empty = "Принятых ответов пока нет." if daily else "Проверенных заявок пока нет."
            return f"{text}\n\n{empty}", kb.archive_list([], 0, False, button, daily)
        if page:
            text += f"\nСтраница {page + 1}"
        return text, kb.archive_list(items, page, has_more, button, daily)

    async def _archive_open(
        self, session: AsyncSession, user: User, submission_id: int | None
    ) -> str:
        try:
            submission = await quests_service.get_reviewed(session, user, submission_id or 0)
        except QuestError as exc:
            return str(exc)
        verdict = (
            f"✅ Подтверждена, начислено {coins(submission.earned)}"
            if submission.status == SubmissionStatus.approved
            else "❌ Отклонена"
        )
        text = f"{self._submission_text(submission)}\n\n{verdict}"
        if submission.reviewer:
            text += f"\nПроверил(а): {submission.reviewer.display_name}"
        if submission.reviewed_at:
            text += f"\nДата: {_format_date(submission.reviewed_at)}"
        buttons = (
            kb.override(submission)
            if user.role == Role.admin and submission.quest.kind == QuestKind.photo
            else kb.to_archive()
        )
        await self._send_submission(user, submission, text, buttons)
        return "Заявка открыта ниже"

    async def _notify_admins_reviewed(
        self, session: AsyncSession, submission: Submission
    ) -> None:
        """Учитель проверил ответ на квест админа: админы видят решение и могут его изменить."""
        verdict = (
            "✅ Засчитано" if submission.status == SubmissionStatus.approved else "❌ Отклонено"
        )
        text = (
            f"👀 {ROLE_TITLES[submission.reviewer.role].capitalize()} "
            f"{submission.reviewer.display_name} проверил(а) заявку\n\n"
            f"{self._submission_text(submission)}\n\nРешение: {verdict}"
        )
        for admin in await users_service.admins(session):
            try:
                await self._send_submission(admin, submission, text, kb.override(submission))
            except httpx.HTTPError:
                log.warning("Не удалось уведомить админа %s", admin.max_user_id)

    async def _review_override(
        self, session: AsyncSession, user: User, submission_id: int | None
    ) -> Screen | str:
        try:
            submission, previous, delta = await quests_service.override(
                session, user, submission_id or 0
            )
        except QuestError as exc:
            return str(exc)
        student, quest = submission.user, submission.quest
        approved = submission.status == SubmissionStatus.approved
        if approved:
            await self._check_achievements(session, student)
        if approved:
            result = f"🔄 Решение изменено: засчитано, ученику начислено {coins(delta)}"
            note = (
                f"🔄 Решение по квесту «{quest.title}» пересмотрено: квест засчитан! "
                f"+{coins(delta)}\nБаланс: {coins(student.balance)}"
            )
        else:
            result = f"🔄 Решение изменено: не засчитано, списано {coins(-delta)}"
            if -delta < submission.earned:
                result += f" из {coins(submission.earned)}: остальное ученик уже потратил"
            note = (
                f"🔄 Решение по квесту «{quest.title}» пересмотрено: ответ не засчитан, "
                f"списано {coins(-delta)}.\nБаланс: {coins(student.balance)}"
            )
        try:
            await self._send(student.max_user_id, note, kb.to_quests())
        except httpx.HTTPError:
            log.warning("Не удалось уведомить ученика %s", student.max_user_id)
        if (
            previous is not None
            and previous.id != user.id
            and previous.role in (Role.teacher, Role.moderator)
        ):
            try:
                await self._send(
                    previous.max_user_id,
                    f"🔄 Админ {user.display_name} изменил(а) ваше решение по заявке "
                    f"№{submission.id} ({student.display_name}, «{quest.title}»): "
                    f"{'засчитано' if approved else 'не засчитано'}.",
                    kb.back_to_menu(),
                )
            except httpx.HTTPError:
                log.warning("Не удалось уведомить учителя %s", previous.max_user_id)
        return (
            f"{self._submission_text(submission)}\n\n{result}",
            self._submission_media(submission) + kb.override(submission),
        )

    @staticmethod
    def _review_keyboard(submission: Submission) -> list[dict]:
        quest = submission.quest
        if quest.reward_min is None:
            return kb.review_actions(submission.id)
        return kb.review_amounts(submission.id, quests_service.reward_options(quest))

    async def _pending_for_review(self, session: AsyncSession, user: User, submission_id: int):
        items = {s.id: s for s in await quests_service.pending_for(session, user)}
        return items.get(submission_id)

    async def _reject_prompt(self, session: AsyncSession, user: User, submission_id: int) -> Screen | str:
        submission = await self._pending_for_review(session, user, submission_id)
        if submission is None:
            return "Заявка уже проверена или не найдена."
        await users_service.set_state(session, user, ST_REJECT_REASON, {"submission_id": submission.id})
        return (
            f"{self._submission_text(submission)}\n\n"
            "❌ Почему отклоняете? Выберите причину или напишите свою сообщением — "
            "её увидит ученик.",
            self._submission_media(submission)
            + kb.reject_reasons(submission.id, quests_service.REJECT_REASONS),
        )

    async def _reject_write(self, session: AsyncSession, user: User, submission_id: int) -> Screen | str:
        submission = await self._pending_for_review(session, user, submission_id)
        if submission is None:
            return "Заявка уже проверена или не найдена."
        await users_service.set_state(session, user, ST_REJECT_REASON, {"submission_id": submission.id})
        return (
            f"{self._submission_text(submission)}\n\n"
            "✍️ Напишите причину отклонения одним сообщением — её увидит ученик "
            f"(до {quests_service.REASON_MAX} символов).",
            self._submission_media(submission) + kb.reject_write(submission.id),
        )

    async def _review_back(self, session: AsyncSession, user: User, submission_id: int) -> Screen | str:
        await users_service.set_state(session, user, None)
        submission = await self._pending_for_review(session, user, submission_id)
        if submission is None:
            return "Заявка уже проверена или не найдена."
        return (
            self._submission_text(submission),
            self._submission_media(submission) + self._review_keyboard(submission),
        )

    async def _review_decide(
        self,
        session: AsyncSession,
        user: User,
        submission_id: int | None,
        approve: bool,
        amount: int | None = None,
        comment: str | None = None,
    ) -> Screen | str:
        try:
            submission = await quests_service.review(
                session, user, submission_id or 0, approve, amount, comment
            )
        except QuestError as exc:
            return str(exc)
        student, quest = submission.user, submission.quest
        if approve:
            result = f"✅ Подтверждено, ученику начислено {coins(submission.earned)}"
            note = (
                f"✅ Квест «{quest.title}» засчитан! +{coins(submission.earned)}\n"
                f"Баланс: {coins(student.balance)}"
            )
            note += self._again_note(quest)
        else:
            result = "❌ Отклонено, ученик может прислать новый ответ"
            note = f"❌ Ответ по квесту «{quest.title}» не принят."
            if submission.review_comment:
                result += f"\nПричина: {submission.review_comment}"
                note += f"\nПричина: {submission.review_comment}"
            note += "\nМожно выполнить задание ещё раз и отправить новый ответ."
        try:
            await self._send(student.max_user_id, note, kb.to_quests())
        except httpx.HTTPError:
            log.warning("Не удалось уведомить ученика %s", student.max_user_id)
        if approve:
            await self._check_achievements(session, student)
        if user.role == Role.moderator or (
            user.role == Role.teacher and quests_service.is_admin_quest(quest)
        ):
            await self._notify_admins_reviewed(session, submission)
        if user.role == Role.moderator:
            audit.record(
                user, "submission.approve" if approve else "submission.reject",
                f"заявка №{submission.id}: {student.display_name}, «{quest.title}»",
            )
        if approve and await pub_service.offer(session, submission):
            return await self._publication_screen(session, submission, result)
        # Вложения и текст ответа оставляем в сообщении, меняем итог и кнопки
        return (
            f"{self._submission_text(submission)}\n\n{result}",
            self._submission_media(submission) + kb.to_review(),
        )
