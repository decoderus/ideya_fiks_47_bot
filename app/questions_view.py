"""Вопросы учеников: «❓ Задать вопрос» в карточке квеста и в меню ученика (общий вопрос),
раздел «❓ Вопросы» у персонала."""
from sqlalchemy.ext.asyncio import AsyncSession

from app import keyboards as kb
from app.db.models import QuestQuestion, Role, User
from app.keyboards import ROLE_TITLES
from app.services import audit
from app.services import questions as questions_service
from app.services import users as users_service
from app.services.questions import QuestionError
from app.ui import Screen, parse_id

ST_QA_ASK = "qa_ask"
ST_QA_ANSWER = "qa_answer"
QUESTION_STATES = (ST_QA_ASK, ST_QA_ANSWER)


def question_text(question: QuestQuestion) -> str:
    student = question.student
    school_class = f", {student.school_class.name}" if student.school_class else ""
    lines = [
        f"❓ Вопрос{questions_service.about(question)}" if question.quest else "❓ Общий вопрос",
        f"От: {student.display_name}{school_class}",
        "",
        question.text,
    ]
    for answer in question.answers:
        lines += ["", f"💬 {answer.text}", questions_service.signature(answer)]
    return "\n".join(lines)


class QuestionsMixin:
    """Часть класса Bot: вопросы учеников и ответы на них."""

    async def _questions(self, session: AsyncSession, user: User, payload: str) -> Screen | str:
        if payload == "menu:questions":
            return await self._qa_list(session, user)
        question_id = parse_id(payload) or 0
        if payload.startswith("qa:ask:"):
            return await self._qa_ask_start(session, user, question_id)
        if payload.startswith("qa:open:"):
            return await self._qa_card(session, user, question_id)
        if payload.startswith("qa:ans:"):
            return await self._qa_answer_start(session, user, question_id)
        return "Неизвестное действие."

    # --- ученик ---

    async def _qa_ask_start(self, session: AsyncSession, user: User, quest_id: int) -> Screen | str:
        if not quest_id:
            # Общий вопрос из меню ученика
            if user.role != Role.student:
                return "Вопросы задают ученики."
            await users_service.set_state(session, user, ST_QA_ASK, {"quest_id": None})
            return (
                "❓ Задать вопрос\n\n"
                "Напишите вопрос одним сообщением — о боте, монетах, наградах или о чём-то другом. "
                "Его получат модераторы и администраторы школы, ответ придёт в этот чат.\n\n"
                "Вопрос по конкретному квесту удобнее задать из его карточки — "
                "тогда его увидит и учитель, который создал квест.",
                kb.cancel(),
            )
        try:
            quest = await questions_service.quest_for_question(session, user, quest_id)
        except QuestionError as exc:
            return str(exc)
        await users_service.set_state(session, user, ST_QA_ASK, {"quest_id": quest.id})
        return (
            f"❓ Вопрос по квесту «{quest.title}»\n\n"
            "Напишите вопрос одним сообщением — по заданию или по проверке ответа. "
            "Его получат учитель — автор квеста и администрация школы, ответ придёт в этот чат.",
            kb.cancel(),
        )

    async def _question_step(self, session: AsyncSession, user: User, text: str) -> Screen:
        if not text or text.startswith("/"):
            return "Напишите текстом одним сообщением или нажмите «Отмена».", kb.cancel()
        data = user.state_data or {}
        if user.state == ST_QA_ASK:
            try:
                question = await questions_service.ask(session, user, data.get("quest_id"), text)
            except QuestionError as exc:
                return str(exc), kb.cancel()
            await users_service.set_state(session, user, None)
            await self._notify(
                await questions_service.recipients(session, question),
                question_text(question),
                kb.question_notice(question.id),
            )
            return (
                "✅ Вопрос отправлен. Ответ придёт в этот чат.",
                kb.to_quests() if question.quest else kb.back_to_menu(),
            )
        return await self._qa_answer(session, user, data.get("question_id", 0), text)

    # --- админ, модератор, учитель — автор квеста ---

    async def _qa_list(self, session: AsyncSession, user: User) -> Screen | str:
        if user.role not in (Role.admin, Role.moderator, Role.teacher):
            return "Раздел доступен учителям, модераторам и админам."
        items = await questions_service.for_staff(session, user)
        if not items:
            return "❓ Вопросов учеников пока нет.", kb.back_to_menu()
        waiting = sum(1 for q in items if not q.answers)
        text = (
            f"❓ Вопросы учеников\nЖдут ответа: {waiting}\n"
            "🆕 — без ответа, ✅ — отвечены. Последние сверху."
        )
        if user.role == Role.teacher:
            text += "\nЗдесь вопросы по вашим квестам."
        return text, kb.question_list(items)

    def _qa_action(self, user: User, question: QuestQuestion) -> str | None:
        if questions_service.answer_block(user, question):
            return None
        return "✍️ Уточнить ответ" if questions_service.is_clarification(user, question) else "✍️ Ответить"

    async def _qa_card(self, session: AsyncSession, user: User, question_id: int) -> Screen | str:
        try:
            question = await questions_service.get(session, question_id)
        except QuestionError as exc:
            return str(exc)
        if not questions_service.can_see(user, question):
            return "Вопрос не найден."
        return question_text(question), kb.question_card(question.id, self._qa_action(user, question))

    async def _qa_answer_start(
        self, session: AsyncSession, user: User, question_id: int
    ) -> Screen | str:
        try:
            question = await questions_service.get(session, question_id)
        except QuestionError as exc:
            return str(exc)
        block = questions_service.answer_block(user, question)
        if block:
            return block
        await users_service.set_state(session, user, ST_QA_ANSWER, {"question_id": question.id})
        prompt = (
            "Напишите уточнение одним сообщением — ученик получит его как уточнение администратора."
            if questions_service.is_clarification(user, question)
            else "Напишите ответ одним сообщением."
        )
        return (
            f"{question_text(question)}\n\n✍️ {prompt} "
            f"Ученик увидит ваше имя и роль ({ROLE_TITLES[user.role]}).",
            kb.cancel(),
        )

    async def _qa_answer(self, session: AsyncSession, user: User, question_id: int, text: str) -> Screen:
        try:
            answer = await questions_service.answer(session, user, question_id, text)
        except QuestionError as exc:
            return str(exc), kb.cancel()
        await users_service.set_state(session, user, None)
        question = await questions_service.get(session, question_id)
        clarification = len(question.answers) > 1
        header = "💬 Уточнение ответа на ваш вопрос" if clarification else "💬 Ответ на ваш вопрос"
        await self._notify(
            [question.student],
            f"{header}{questions_service.about(question)}\n\n❓ {question.text}\n\n"
            f"💬 {answer.text}\n{questions_service.signature(answer)}",
            kb.new_quest_notice(question.quest_id) if question.quest else kb.back_to_menu(),
        )
        if answer.author_role != Role.admin:
            # Админ видит ответ нижестоящего и может его уточнить
            student = question.student
            await self._notify(
                await users_service.admins(session),
                f"💬 {answer.author.display_name} ({ROLE_TITLES[answer.author_role]}) ответил(а) "
                f"на вопрос ученика {student.display_name}{questions_service.about(question)}.\n\n"
                f"❓ {question.text}\n\n💬 {answer.text}\n\nПри необходимости уточните ответ.",
                kb.question_notice(question.id, clarify=True),
            )
        if user.role in (Role.admin, Role.moderator):
            kind = "уточнение" if clarification else "ответ"
            topic = f"«{question.quest.title}»" if question.quest else "общий"
            audit.record(user, "question.answer", f"{kind} на вопрос №{question.id}, {topic}")
        sent = "Уточнение отправлено" if clarification else "Ответ отправлен"
        return (
            f"✅ {sent} ученику {question.student.display_name}.",
            kb.question_card(question.id, None),
        )
