"""Канал школы: подключение и публикация засчитанных ответов.

Канал подключается сам, когда бота добавляют в него администратором,
или когда админ пересылает боту любой пост из канала.
"""
import logging

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app import keyboards as kb
from app.db.models import Role, Submission, User
from app.services import audit
from app.services import publication as pub_service
from app.services import settings as settings_service
from app.services import users as users_service
from app.services.publication import PublicationError
from app.ui import Screen, find_attachment, find_image, image_attachment, parse_id, video_attachment

log = logging.getLogger(__name__)

ST_PUB_NOTE = "pub_note"  # проверяющий пишет, что доработать перед публикацией
ST_PUB_FIX = "pub_fix"  # автор присылает исправленный вариант


def _media(photo: dict | None, video: dict | None) -> list[dict]:
    media = []
    if photo:
        media.append(image_attachment(photo))
    if video:
        media.append(video_attachment(video))
    return media


class ChannelMixin:
    """Часть класса Bot: канал школы и публикации."""

    # --- подключение канала ---

    async def _connect_channel(self, session: AsyncSession, chat_id: int) -> str:
        try:
            title = (await self.client.get_chat(chat_id)).get("title") or "канал школы"
        except httpx.HTTPError:
            title = "канал школы"
        await settings_service.set_channel(session, chat_id, title)
        log.info("Подключён канал %s «%s»", chat_id, title)
        return title

    async def _on_bot_added(self, update: dict) -> None:
        """bot_added или bot_admin_permissions_changed: бота добавили в канал или дали права."""
        chat_id = update.get("chat_id")
        log.info("Событие %s: chat_id=%s, is_channel=%s", update.get("update_type"), chat_id, update.get("is_channel"))
        if chat_id is None:
            return
        if not update.get("is_channel"):
            # В событии о правах признака канала может не быть — уточняем тип чата
            try:
                if (await self.client.get_chat(chat_id)).get("type") != "channel":
                    return
            except httpx.HTTPError:
                return
        async with self.sessions() as session:
            current = await settings_service.channel(session)
            title = await self._connect_channel(session, chat_id)
            if current and current[0] == chat_id:
                return  # канал уже подключён — не повторяем уведомление
            await self._notify(
                await users_service.admins(session),
                f"📢 Бот подключён к каналу «{title}» — сюда будут публиковаться "
                "ответы, которые проверяющие отметят для публикации.",
                kb.back_to_menu(),
            )

    async def _channel_from_forward(self, session: AsyncSession, user: User, link: dict) -> Screen | None:
        """Админ переслал боту пост из канала — подключаем этот канал."""
        chat_id = link.get("chat_id")
        if user.role != Role.admin or link.get("type") != "forward" or not chat_id:
            return None
        title = await self._connect_channel(session, chat_id)
        audit.record(user, "channel.set", f"«{title}» (chat_id {chat_id})")
        return (
            f"📢 Канал «{title}» подключён. Сюда будут публиковаться ответы, "
            "которые проверяющие отметят для публикации.\n"
            "Бот должен быть администратором канала с правом публиковать сообщения.",
            kb.back_to_menu(),
        )

    # --- решение проверяющего ---

    async def _publication_offer(self, session: AsyncSession, submission: Submission) -> str:
        channel = await settings_service.channel(session)
        title = channel[1] if channel else "канал школы"
        return f"📢 Опубликовать этот ответ в канале «{title}»?"

    async def _publication_screen(self, session: AsyncSession, submission: Submission, result: str = "") -> Screen:
        """Засчитанный ответ с вопросом о публикации и кнопками решения."""
        _, photo, video = pub_service.content(submission)
        if result:
            head = f"{self._submission_text(submission)}\n\n{result}"
        else:
            head = f"Так будет выглядеть пост:\n\n{pub_service.post_text(submission)}"
        return (
            f"{head}\n\n{await self._publication_offer(session, submission)}",
            _media(photo, video) + kb.publication_actions(submission.id),
        )

    async def _publication(self, session: AsyncSession, user: User, payload: str) -> Screen | str:
        _, action, *_ = payload.split(":")
        submission_id = parse_id(payload) or 0
        try:
            if action == "send":
                return await self._publication_fix_prompt(session, user, submission_id)
            submission = await pub_service.for_decision(session, user, submission_id)
            if action == "back":
                await users_service.set_state(session, user, None)
                return await self._publication_screen(session, submission)
            if action == "ok":
                return await self._publish(session, user, submission)
            if action == "no":
                await pub_service.mark(session, submission, pub_service.SKIPPED)
                self._audit_publication(user, submission, "submission.pub_skip")
                return self._publication_done(submission, "🚫 Решено не публиковать.")
            if action == "fix":
                await users_service.set_state(session, user, ST_PUB_NOTE, {"submission_id": submission.id})
                return (
                    f"✏️ Что исправить автору перед публикацией?\n\n"
                    "Напишите одним сообщением — например, какие ошибки поправить. "
                    "Ученик получит комментарий и пришлёт исправленный вариант.",
                    kb.publication_note(submission.id),
                )
        except PublicationError as exc:
            return str(exc)
        return "Неизвестное действие."

    def _audit_publication(self, user: User, submission: Submission, action: str, extra: str = "") -> None:
        if user.role in (Role.admin, Role.moderator):
            audit.record(
                user, action,
                f"заявка №{submission.id}: {submission.user.display_name}, «{submission.quest.title}»{extra}",
            )

    @staticmethod
    def _publication_done(submission: Submission, note: str) -> Screen:
        return (
            f"Заявка №{submission.id}: {submission.user.display_name}, «{submission.quest.title}»\n\n{note}",
            kb.to_review(),
        )

    async def _publish(self, session: AsyncSession, user: User, submission: Submission) -> Screen | str:
        channel = await settings_service.channel(session)
        if channel is None:
            return "Канал школы не подключён: добавьте бота в канал администратором."
        chat_id, title = channel
        _, photo, video = pub_service.content(submission)
        try:
            await self.client.send_to_chat(chat_id, pub_service.post_text(submission), _media(photo, video))
        except httpx.HTTPError as exc:
            log.warning("Не удалось опубликовать заявку %s в канале: %s", submission.id, exc)
            return "Не удалось опубликовать: проверьте, что бот — администратор канала с правом публикации."
        await pub_service.mark(session, submission, pub_service.PUBLISHED)
        self._audit_publication(user, submission, "submission.publish", f" → «{title}»")
        try:
            await self._send(
                submission.user.max_user_id,
                f"📢 Ваш ответ на квест «{submission.quest.title}» опубликован в канале «{title}»!",
                kb.back_to_menu(),
            )
        except httpx.HTTPError:
            log.warning("Не удалось уведомить ученика %s", submission.user.max_user_id)
        return self._publication_done(submission, f"📢 Опубликовано в канале «{title}».")

    async def _publication_note_text(self, session: AsyncSession, user: User, text: str) -> Screen:
        """Проверяющий написал, что доработать: отправляем автору."""
        if not text or text.startswith("/"):
            return "Напишите комментарий текстом или нажмите «« Назад».", None
        submission_id = (user.state_data or {}).get("submission_id", 0)
        try:
            submission = await pub_service.for_decision(session, user, submission_id)
            await pub_service.mark(session, submission, pub_service.REWORK, text)
        except PublicationError as exc:
            return str(exc), None
        await users_service.set_state(session, user, None)
        self._audit_publication(user, submission, "submission.pub_rework", f": {submission.publication_note}")
        try:
            await self._send(
                submission.user.max_user_id,
                f"✏️ Ваш ответ на квест «{submission.quest.title}» хотят опубликовать в канале школы, "
                "но сначала его нужно доработать.\n\n"
                f"Комментарий проверяющего: {submission.publication_note}\n\n"
                "Монеты уже начислены. Нажмите кнопку ниже и пришлите исправленный вариант.",
                kb.publication_fix(submission.id),
            )
        except httpx.HTTPError:
            log.warning("Не удалось уведомить ученика %s", submission.user.max_user_id)
        return self._publication_done(
            submission, "✏️ Отправлено автору на доработку. Исправленный вариант придёт вам сообщением."
        )

    # --- доработка автором ---

    async def _publication_fix_prompt(self, session: AsyncSession, user: User, submission_id: int) -> Screen:
        submission = await pub_service.for_fix(session, user, submission_id)
        await users_service.set_state(session, user, ST_PUB_FIX, {"submission_id": submission.id})
        current, _, _ = pub_service.content(submission)
        shown = f"\n\nВаш текст сейчас:\n{current}" if current else ""
        return (
            f"✍️ Пришлите исправленный вариант одним сообщением: текст, фото или видео.\n\n"
            f"Комментарий проверяющего: {submission.publication_note}{shown}",
            kb.cancel(),
        )

    async def _publication_fix(
        self, session: AsyncSession, user: User, text: str, attachments: list[dict]
    ) -> Screen:
        submission_id = (user.state_data or {}).get("submission_id", 0)
        photo, video = find_image(attachments), find_attachment(attachments, "video")
        try:
            submission = await pub_service.save_fix(session, user, submission_id, text, photo, video)
        except PublicationError as exc:
            return str(exc), kb.cancel()
        await users_service.set_state(session, user, None)
        reviewer = submission.reviewer
        recipients = [reviewer] if reviewer and reviewer.role != Role.student else await users_service.admins(session)
        offer = await self._publication_offer(session, submission)
        await self._notify(
            recipients,
            f"✏️ {submission.user.display_name} доработал(а) ответ на квест «{submission.quest.title}» "
            f"(заявка №{submission.id}).\n\nТак будет выглядеть пост:\n\n"
            f"{pub_service.post_text(submission)}\n\n{offer}",
            _media(photo, video) + kb.publication_actions(submission.id),
        )
        return "📨 Исправленный вариант отправлен проверяющему.", kb.back_to_menu()
