"""Опрос: прохождение (ученик, учитель) и итоги с рассылкой приглашений (админ)."""
import asyncio
import logging

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app import keyboards as kb
from app.db.models import Role, User
from app.keyboards import coins
from app.services import audit
from app.services import survey as survey_service
from app.services import users as users_service
from app.services.survey import SurveyError
from app.ui import Screen

log = logging.getLogger("aktiv.survey")

ST_SURVEY = "survey"
ST_SURVEY_TEXT = "survey_text"
SURVEY_STATES = (ST_SURVEY, ST_SURVEY_TEXT)

INVITE = {
    Role.student: (
        "📋 Помоги сделать бот лучше!\n"
        "4 коротких вопроса — меньше минуты. За участие — +100 монет 🎁\n"
        "Ответы анонимны: учителя видят только общие итоги."
    ),
    Role.teacher: (
        "📋 Опрос для учителей\n"
        "4 коротких вопроса о том, насколько бот помогает в работе, — меньше минуты.\n"
        "Ответы анонимны: администрация видит только общие итоги."
    ),
}
BROADCAST_INTERVAL = 0.05


def _bar(count: int, total: int) -> str:
    return f"{round(100 * count / total)}% ({count})" if total else "—"


class SurveyMixin:
    """Часть класса Bot: опрос."""

    async def _survey(self, session: AsyncSession, user: User, payload: str) -> Screen | str:
        if payload == "sv:start":
            return await self._survey_start(session, user)
        if payload.startswith("sv:a:"):
            return await self._survey_answer(session, user, payload)
        if payload == "sv:skip":
            return await self._survey_finish(session, user, None)
        # --- админ ---
        if user.role != Role.admin:
            return "Итоги опроса видит администратор."
        if payload == "sv:admin":
            return await self._survey_admin(session)
        if payload == "sv:send":
            return await self._survey_send(session, user)
        if payload == "sv:close":
            await survey_service.set_open(session, False)
            audit.record(user, "survey.close", survey_service.SURVEY)
            return await self._survey_admin(session, "🔒 Опрос закрыт: кнопка «📋 Опрос» пропала у всех.")
        if payload.startswith("sv:res:"):
            role = Role.student if payload.endswith("student") else Role.teacher
            return await self._survey_results(session, role)
        if payload == "sv:com":
            return await self._survey_comments(session)
        return "Неизвестное действие."

    # --- прохождение ---

    def _survey_question(self, user: User, index: int) -> Screen:
        questions = survey_service.questions_for(user)
        q = questions[index]
        return (
            f"📋 Опрос · вопрос {index + 1} из {len(questions)}\n\n{q.text}",
            kb.survey_options(index, q.options, nps=q.nps),
        )

    async def _survey_start(self, session: AsyncSession, user: User) -> Screen | str:
        if survey_service.questions_for(user) is None:
            return "Опрос — для учеников и учителей."
        if await survey_service.answered(session, user):
            return "Вы уже прошли опрос. Спасибо!"
        if not await survey_service.is_open(session):
            return "Опрос сейчас закрыт."
        await users_service.set_state(session, user, ST_SURVEY, {"answers": {}})
        return self._survey_question(user, 0)

    async def _survey_answer(self, session: AsyncSession, user: User, payload: str) -> Screen | str:
        try:
            _, _, index, key = payload.split(":", 3)
            index = int(index)
        except ValueError:
            return "Неизвестное действие."
        data = user.state_data or {}
        answers = dict(data.get("answers") or {})
        questions = survey_service.questions_for(user)
        if user.state != ST_SURVEY or questions is None:
            return "Опрос уже пройден или прерван. Начните заново кнопкой «📋 Опрос»."
        if index != len(answers) or index >= len(questions):
            return "Ответьте на текущий вопрос."
        q = questions[index]
        if key not in {k for k, _ in q.options}:
            return "Выберите вариант кнопкой."
        answers[q.key] = key
        if len(answers) < len(questions):
            await users_service.set_state(session, user, ST_SURVEY, {"answers": answers})
            return self._survey_question(user, len(answers))
        await users_service.set_state(session, user, ST_SURVEY_TEXT, {"answers": answers})
        return (
            "📋 Последний вопрос\n\nЧто добавить в бот или улучшить? Напишите одним сообщением — "
            "или нажмите «Пропустить».",
            kb.survey_skip(),
        )

    async def _survey_text(self, session: AsyncSession, user: User, text: str) -> Screen:
        if user.state == ST_SURVEY:
            return "Выберите ответ кнопкой под вопросом.", None
        if not text or text.startswith("/"):
            return "Напишите пожелание текстом или нажмите «Пропустить».", kb.survey_skip()
        screen = await self._survey_finish(session, user, text)
        return (screen, kb.back_to_menu()) if isinstance(screen, str) else screen

    async def _survey_finish(self, session: AsyncSession, user: User, comment: str | None) -> Screen | str:
        if user.state != ST_SURVEY_TEXT:
            return "Опрос уже пройден или прерван."
        answers = (user.state_data or {}).get("answers") or {}
        try:
            reward = await survey_service.submit(session, user, answers, comment)
        except SurveyError as exc:
            await users_service.set_state(session, user, None)
            return str(exc)
        await users_service.set_state(session, user, None)
        text = "✅ Спасибо! Ваши ответы помогут сделать бот лучше."
        if reward:
            text += f"\n🎁 +{coins(reward)}. Баланс: {coins(user.balance)}"
        return text, kb.back_to_menu()

    # --- админ ---

    async def _survey_admin(self, session: AsyncSession, note: str = "") -> Screen:
        opened = await survey_service.is_open(session)
        waiting = len(await survey_service.recipients(session))
        students = await survey_service.results(session, Role.student)
        teachers = await survey_service.results(session, Role.teacher)
        lines = [note, ""] if note else []
        lines += [
            "📋 Опрос учеников и учителей",
            f"Статус: {'открыт ✅' if opened else 'закрыт'}",
            f"Ответили: учеников — {students.count}, учителей — {teachers.count}",
            f"Ещё не прошли: {waiting}",
        ]
        if students.nps is not None:
            lines.append(f"NPS учеников: {students.nps:+d}")
        if teachers.nps is not None:
            lines.append(f"NPS учителей: {teachers.nps:+d}")
        lines += [
            "",
            "«Отправить» открывает опрос и шлёт приглашение тем, кто ещё не прошёл. "
            "Ученик за прохождение получает 100 монет.",
        ]
        return "\n".join(lines), kb.survey_admin(opened, waiting)

    async def _survey_send(self, session: AsyncSession, admin: User) -> Screen:
        await survey_service.set_open(session, True)
        targets = [(u.max_user_id, u.role) for u in await survey_service.recipients(session)]
        audit.record(admin, "survey.send", f"{survey_service.SURVEY}, приглашений: {len(targets)}")

        async def send_all() -> None:
            for i, (max_id, role) in enumerate(targets):
                if i:
                    await asyncio.sleep(BROADCAST_INTERVAL)
                try:
                    await self._send(max_id, INVITE[role], kb.survey_invite())
                except httpx.HTTPError:
                    log.warning("Не доставлено приглашение в опрос %s", max_id)

        await self._run_later(send_all())
        return await self._survey_admin(
            session, f"📨 Приглашения отправляются: {len(targets)} — тем, кто ещё не прошёл опрос."
        )

    async def _survey_results(self, session: AsyncSession, role: Role) -> Screen:
        res = await survey_service.results(session, role)
        who = "ученики" if role == Role.student else "учителя"
        lines = [f"📋 Итоги опроса · {who}", f"Ответили: {res.count}"]
        if not res.count:
            return "\n".join(lines + ["", "Ответов пока нет."]), kb.survey_back()
        for q in survey_service.QUESTIONS[role]:
            counter = res.distribution[q.key]
            lines += ["", q.text.split(" 0 —")[0]]
            if q.nps:
                scores = [int(k) for k, n in counter.items() for _ in range(n)]
                lines += [
                    f"💚 Посоветуют (9–10): {_bar(sum(1 for s in scores if s >= 9), res.count)}",
                    f"😐 Нейтральны (7–8): {_bar(sum(1 for s in scores if 7 <= s <= 8), res.count)}",
                    f"🔻 Критики (0–6): {_bar(sum(1 for s in scores if s <= 6), res.count)}",
                    f"NPS: {res.nps:+d}" if res.nps is not None else "NPS: —",
                ]
            else:
                lines += [f"{label} — {_bar(counter[key], res.count)}" for key, label in q.options]
        return "\n".join(lines), kb.survey_back()

    async def _survey_comments(self, session: AsyncSession) -> Screen:
        lines = ["💬 Пожелания из опроса (без имён, новые сверху)"]
        for role, who in ((Role.student, "Ученики"), (Role.teacher, "Учителя")):
            res = await survey_service.results(session, role)
            if res.comments:
                lines += ["", f"{who}:"] + [f"• {c}" for c in res.comments[:20]]
        if len(lines) == 1:
            lines.append("\nПожеланий пока нет.")
        text = "\n".join(lines)
        return (text[:3900] + "…") if len(text) > 3900 else text, kb.survey_back()
