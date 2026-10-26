"""Проверка ответов: «На проверке», архив, засчитать или отклонить, отмена решения админом."""
import logging

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app import keyboards as kb
from app.db.models import QuestKind, Role, Submission, SubmissionStatus, User
from app.keyboards import ROLE_TITLES, coins
from app.services import audit
from app.services import publication as pub_service
from app.services import quests as quests_service
from app.services import users as users_service
from app.services.quests import QuestError
from app.ui import Screen, format_date, image_attachment, parse_id, video_attachment

log = logging.getLogger(__name__)

ST_REJECT_REASON = "reject_reason"  # проверяющий пишет причину отклонения

KEEP_STATE = ("rev:nr:", "rev:nw:")


class ReviewMixin:
    """Часть класса Bot: проверка ответов."""

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
            media.append(image_attachment(submission.photo))
        if submission.video:
            media.append(video_attachment(submission.video))
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
            text += f"\nДата: {format_date(submission.reviewed_at)}"
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

    # --- маршрут раздела и причина отклонения ---

    async def _review_callback(self, session: AsyncSession, user: User, payload: str) -> Screen | str:
        if payload == "menu:review":
            return await self._review_list(session, user)
        if payload.startswith("rev:open:"):
            return await self._review_open(session, user, parse_id(payload))
        if payload.startswith("rev:flip:"):
            return await self._review_override(session, user, parse_id(payload))
        if payload.startswith("rev:no:"):
            return await self._reject_prompt(session, user, parse_id(payload) or 0)
        if payload.startswith("rev:nw:"):
            return await self._reject_write(session, user, parse_id(payload) or 0)
        if payload.startswith("rev:back:"):
            return await self._review_back(session, user, parse_id(payload) or 0)
        if payload.startswith("rev:nr:"):
            return await self._reject_with_reason(session, user, payload)
        if payload.startswith("rev:ok:"):
            parts = payload.split(":")
            try:
                submission_id = int(parts[2])
                amount = int(parts[3]) if len(parts) > 3 else None
            except (IndexError, ValueError):
                return "Неизвестное действие."
            return await self._review_decide(session, user, submission_id, True, amount)
        if payload.startswith("arch:page:"):
            return await self._archive_list(session, user, parse_id(payload) or 0)
        if payload.startswith("arch:daily:"):
            return await self._archive_list(session, user, parse_id(payload) or 0, daily=True)
        if payload.startswith("arch:open:"):
            return await self._archive_open(session, user, parse_id(payload))
        return "Неизвестное действие."

    async def _reject_with_reason(self, session: AsyncSession, user: User, payload: str) -> Screen | str:
        """«rev:nr:<заявка>:<номер причины>» — отклонить с готовой причиной."""
        ids = payload.split(":")
        try:
            submission_id, choice = int(ids[2]), int(ids[3])
        except (IndexError, ValueError):
            return "Неизвестное действие."
        await users_service.set_state(session, user, None)
        reasons = quests_service.REJECT_REASONS
        reason = reasons[choice - 1] if 1 <= choice <= len(reasons) else None
        return await self._review_decide(session, user, submission_id, False, comment=reason)

    async def _reject_reason_text(self, session: AsyncSession, user: User, text: str) -> Screen:
        """Проверяющий написал причину отклонения своими словами."""
        if not text or text.startswith("/"):
            return "Напишите причину текстом или выберите кнопку под заявкой.", None
        submission_id = (user.state_data or {}).get("submission_id", 0)
        await users_service.set_state(session, user, None)
        result = await self._review_decide(session, user, submission_id, False, comment=text)
        return (result, None) if isinstance(result, str) else result
