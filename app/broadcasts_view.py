"""Оповещения: отправка школе, классу или лично, черновики модераторов, «Оповещения» у получателя."""
import logging

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app import keyboards as kb
from app.db.models import DraftStatus, Role, User
from app.keyboards import ROLE_TITLES, coins
from app.services import audit
from app.services import broadcasts as broadcasts_service
from app.services import classes as classes_service
from app.services import users as users_service
from app.services.broadcasts import BroadcastError
from app.ui import (
    Screen,
    broadcast_media,
    find_attachment,
    find_image,
    format_date,
    format_day,
    parse_id,
)

log = logging.getLogger(__name__)

ST_BC_TEXT = "bc_text"
ST_BC_AUDIENCE = "bc_audience"
ST_BC_CONFIRM = "bc_confirm"
BROADCAST_STATES = (ST_BC_TEXT, ST_BC_AUDIENCE, ST_BC_CONFIRM)

KEEP_STATE = ("bc:aud:", "bc:send")


class BroadcastsMixin:
    """Часть класса Bot: оповещения."""

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
            return await self._broadcast_history(session, max(parse_id(payload) or 0, 0))
        if payload.startswith("bc:open:"):
            return await self._broadcast_open(session, user, parse_id(payload) or 0)
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
        photo = find_image(attachments)
        audio = find_attachment(attachments, "audio")
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
        photo = broadcast_media(data.get("photo"), data.get("audio"))
        if user.role == Role.moderator:
            text += "\nОтправится после подтверждения администратора."
        return text, photo + kb.broadcast_confirm(draft=user.role == Role.moderator)

    async def _broadcast_pick_student(self, session: AsyncSession, value: str) -> Screen | str:
        try:
            _, class_id, page = value.split(":")
            class_id, page = int(class_id), max(int(page), 0)
        except ValueError:
            class_id, page = parse_id(value) or 0, 0
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
        photo = broadcast_media(broadcast.photo, broadcast.audio)
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
        photo = broadcast_media(draft.photo, draft.audio)
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
            draft = await broadcasts_service.pending_draft(session, user, parse_id(payload) or 0)
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
        photo = broadcast_media(draft.photo, draft.audio)
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
        photo = broadcast_media(broadcast.photo, broadcast.audio)
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
        return title, kb.inbox_list(items, page, has_more, format_day)

    async def _inbox_open(
        self, session: AsyncSession, user: User, broadcast_id: int
    ) -> str:
        broadcast, reward = await broadcasts_service.mark_read(session, user, broadcast_id)
        if broadcast is None:
            return "Оповещение не найдено."
        text = (
            f"{await self._broadcast_text(session, broadcast)}\n\n"
            f"Получено: {format_date(broadcast.created_at)}"
        )
        if reward:
            text += f"\n💰 +{coins(reward)} за прочтение. Баланс: {coins(user.balance)}"
        photo = broadcast_media(broadcast.photo, broadcast.audio)
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
            f"Отправлено: {format_date(broadcast.created_at)}"
        )
        photo = broadcast_media(broadcast.photo, broadcast.audio)
        await self._send(user.max_user_id, text, photo + kb.to_broadcasts())
        return "Оповещение открыто ниже"

    # --- маршрут «Оповещений» у получателя ---

    async def _inbox_callback(self, session: AsyncSession, user: User, payload: str) -> Screen | str:
        if payload.startswith("bc:read:"):
            return await self._broadcast_read(session, user, parse_id(payload) or 0)
        if payload.startswith("inbox:open:"):
            return await self._inbox_open(session, user, parse_id(payload) or 0)
        return await self._inbox(session, user, max(parse_id(payload) or 0, 0))
