"""Редактирование квеста: название, описание, награда, периодичность, картинка.

Учитель меняет свои квесты (награда 50–150, у модератора 50–300), админ — любые.
Тип квеста и аудиторию не меняем: это путает заявки, которые уже ждут проверки.
"""
from sqlalchemy.ext.asyncio import AsyncSession

from app import keyboards as kb
from app.db.models import QuestRepeat, Role, User
from app.services import audit
from app.services import quests as quests_service
from app.services import teachers as teachers_service
from app.services import users as users_service
from app.services.quests import QuestError
from app.ui import Screen, find_image

ST_QE_TEXT = "qe_text"  # название, описание или награда — поле в state_data
ST_QE_PHOTO = "qe_photo"
QUEST_EDIT_STATES = (ST_QE_TEXT, ST_QE_PHOTO)

FIELD_PROMPTS = {
    "title": "✏️ Новое название квеста",
    "description": "📝 Новое описание квеста",
    "reward": "💰 Новая награда в монетах",
}


class QuestEditMixin:
    """Часть класса Bot: экраны «✏️ Изменить» в карточке квеста."""

    async def _quest_edit(
        self, session: AsyncSession, user: User, action: str, rest: list[str], quest_id: int
    ) -> Screen | str:
        """action: edit | ef (поле) | rep (периодичность) | photo_rm."""
        quest = await quests_service.get_managed(session, user, quest_id)
        if action == "edit":
            return self._quest_edit_menu(quest)
        if action == "ef":
            field = rest[0] if rest else ""
            if field == "repeat":
                current = kb.REPEAT_TITLES[quest.repeat].lower()
                return (
                    f"🔁 Как часто можно выполнять «{quest.title}»? Сейчас: {current}.",
                    kb.quest_edit_repeats(quest.id),
                )
            if field == "reviewers":
                return await self._reviewers_screen(session, quest)
            if field == "photo":
                await users_service.set_state(session, user, ST_QE_PHOTO, {"quest_id": quest.id})
                return (
                    "🖼 Пришлите новую картинку для квеста (как фото, не файлом).",
                    kb.quest_edit_photo(quest.id, has_photo=bool(quest.photo)),
                )
            if field not in FIELD_PROMPTS:
                return "Это поле изменить нельзя."
            current = getattr(quest, field)
            prompt = f"{FIELD_PROMPTS[field]}.\nСейчас: {current}"
            if field == "reward":
                prompt = f"{FIELD_PROMPTS[field]}.\nСейчас: {kb.reward_text(quest)}"
                low, high = quests_service.reward_bounds(user)
                prompt += (
                    f"\nЧисло от {low} до {high} или, для квеста "
                    f"с проверкой, диапазон, например {low}-{min(high, 500)}. "
                    "Новая награда действует и для заявок, которые ждут проверки; "
                    "уже начисленные монеты не меняются."
                )
            await users_service.set_state(
                session, user, ST_QE_TEXT, {"quest_id": quest.id, "field": field}
            )
            return prompt, kb.cancel()
        if action == "rep":
            value = rest[0] if rest else ""
            try:
                QuestRepeat(value)
            except ValueError:
                return "Неизвестная периодичность."
            quest = await quests_service.update_quest(session, user, quest.id, "repeat", value)
            self._audit_edit(user, quest, f"периодичность → {value}")
            return await self._managed_card(
                session, user, quest, f"✅ Периодичность: {kb.REPEAT_TITLES[quest.repeat].lower()}."
            )
        if action == "rv":
            teacher_id = int(rest[0]) if rest and rest[0].isdigit() else 0
            quest = await quests_service.toggle_reviewer(session, user, quest.id, teacher_id)
            names = await quests_service.reviewer_names(session, quest)
            self._audit_edit(user, quest, f"проверяющие → {names or 'не назначены'}")
            return await self._reviewers_screen(session, quest)
        if action == "photo_rm":
            await users_service.set_state(session, user, None)
            quest = await quests_service.update_quest(session, user, quest.id, "photo", None)
            self._audit_edit(user, quest, "картинка убрана")
            return await self._managed_card(session, user, quest, "🗑 Картинка убрана.")
        return "Неизвестное действие."

    @staticmethod
    def _audit_edit(user: User, quest, details: str) -> None:
        if user.role in (Role.moderator, Role.admin):
            audit.record(user, "quest.edit", f"№{quest.id} «{quest.title}»: {details}")

    async def _reviewers_screen(self, session: AsyncSession, quest) -> Screen:
        teachers = await teachers_service.teachers(session)
        names = await quests_service.reviewer_names(session, quest)
        text = (
            f"👩‍🏫 Проверяющие квеста «{quest.title}»: {names or 'не назначены'}\n\n"
            "Если отметить учителей, ответы из учителей будут получать и проверять только они "
            "(плюс админ и модераторы). Если никого не отмечать — обычные правила: "
            "учителя класса ученика и автор; у ежедневных квестов — только админ и модераторы."
        )
        if not teachers:
            text += "\n\nУчителей в боте пока нет."
        return text, kb.quest_reviewers(quest.id, teachers, set(quest.reviewer_ids or []))

    @staticmethod
    def _quest_edit_menu(quest) -> Screen:
        return (
            f"✏️ Что изменить в квесте «{quest.title}»?\n\n"
            "Тип квеста и то, для кого он, поменять нельзя: для этого закройте квест "
            "и создайте новый.",
            kb.quest_edit_menu(quest.id),
        )

    async def _quest_edit_step(
        self, session: AsyncSession, user: User, text: str, attachments: list[dict]
    ) -> Screen:
        data = user.state_data or {}
        quest_id = data.get("quest_id", 0)
        try:
            if user.state == ST_QE_PHOTO:
                photo = find_image(attachments)
                if photo is None:
                    quest = await quests_service.get_managed(session, user, quest_id)
                    return (
                        "Пришлите картинку как фото (не файлом) или нажмите «Отмена».",
                        kb.quest_edit_photo(quest_id, has_photo=bool(quest.photo)),
                    )
                quest = await quests_service.update_quest(session, user, quest_id, "photo", photo)
                self._audit_edit(user, quest, "новая картинка")
                await users_service.set_state(session, user, None)
                return await self._managed_card(session, user, quest, "✅ Картинка обновлена.")
            # ST_QE_TEXT
            if not text or text.startswith("/"):
                return "Пришлите новое значение текстом или нажмите «Отмена».", kb.cancel()
            field = data.get("field", "")
            quest = await quests_service.update_quest(session, user, quest_id, field, text)
            self._audit_edit(user, quest, f"{field} → {str(getattr(quest, field))[:200]}")
        except QuestError as exc:
            return str(exc), kb.cancel()
        await users_service.set_state(session, user, None)
        notes = {
            "title": f"✅ Новое название: «{quest.title}».",
            "description": "✅ Описание обновлено.",
            "reward": f"✅ Новая награда: {kb.reward_text(quest)}.",
        }
        return await self._managed_card(session, user, quest, notes.get(field, "✅ Сохранено."))
