"""Квесты ученика: список, карточка, «Выполнено», ответ на проверку, вопрос дня."""
import logging

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app import keyboards as kb
from app.db.models import QuestKind, QuestRepeat, Submission, User
from app.keyboards import ROLE_TITLES, coins
from app.services import questions as questions_service
from app.services import quests as quests_service
from app.services import quiz as quiz_service
from app.services import users as users_service
from app.services.questions import QuestionError
from app.services.quests import QuestError
from app.ui import Screen, find_attachment, find_image, image_attachment, parse_id

log = logging.getLogger(__name__)

ST_PHOTO = "photo"  # ученик присылает ответ на квест с проверкой


class QuestsMixin:
    """Часть класса Bot: квесты ученика."""

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
        return [image_attachment(quest.photo)] if quest.photo else []

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
        photo = find_image(attachments)
        video = find_attachment(attachments, "video")
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

    # --- маршрут раздела ---

    async def _quests_callback(self, session: AsyncSession, user: User, payload: str) -> Screen | str:
        if payload == "menu:quests":
            return await self._quest_list(session, user)
        if payload.startswith("q:open:"):
            return await self._quest_card(session, user, parse_id(payload))
        if payload.startswith("q:done:"):
            return await self._quest_done(session, user, parse_id(payload))
        if payload.startswith("q:quiz:"):
            return await self._quiz_answer(session, user, payload)
        if payload.startswith("q:photo:"):
            return await self._quest_photo(session, user, parse_id(payload))
        return "Неизвестное действие."
