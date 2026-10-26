"""Монеты: ручное начисление и списание ученику и всему классу (лимиты учителя — в сервисе)."""
import asyncio
import logging

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app import keyboards as kb
from app.db.models import Role, SchoolClass, User
from app.keyboards import coins
from app.services import classes as classes_service
from app.services import coins as coins_service
from app.services import users as users_service
from app.services.coins import CoinsError
from app.ui import BROADCAST_INTERVAL, Screen, format_day, parse_id

log = logging.getLogger(__name__)

ST_COINS_AMOUNT = "coins_amount"
ST_COINS_REASON = "coins_reason"
ST_COINS_CONFIRM = "coins_confirm"
COINS_STATES = (ST_COINS_AMOUNT, ST_COINS_REASON, ST_COINS_CONFIRM)

KEEP_STATE = ("coins:ok",)


class CoinsMixin:
    """Часть класса Bot: ручные монеты."""

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
            return await self._coins_class_start(session, user, parse_id(payload) or 0)

        try:
            student = await coins_service.get_student(session, user, parse_id(payload) or 0)
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
                    f"{t.amount:+} · {format_day(t.created_at)} · {t.reason} "
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
