"""Экраны демо-режима: вход сразу админом, смена роли командой /demo (кнопки demo:*)."""
from sqlalchemy.ext.asyncio import AsyncSession

from app import demo as demo_service
from app.db.models import Role, User
from app.keyboards import ROLE_TITLES
from app.max_api import callback_button, keyboard
from app.ui import Screen

DEMO_ROLES = (
    (Role.student, "🎒 Ученик"),
    (Role.teacher, "👩‍🏫 Учитель"),
    (Role.moderator, "🛡 Модератор"),
    (Role.admin, "🛠 Админ"),
)

# Настоящий бот, копией которого является демо-версия
ORIGINAL_BOT = "@demo_active_bot"

WELCOME = (
    "🧪 Демо-версия «Сферум. Актив»\n"
    f"Это копия бота {ORIGINAL_BOT}, который уже работает и проходит проверку в настоящей "
    "школе: им пользуются ученики, учителя и администрация. Здесь — его данные, но обезличенные: "
    "ученики и учителя вымышленные, сообщения им никуда не уходят."
)

ROLES_HELP = (
    "🧪 Выберите роль:\n"
    "🎒 Ученик — квесты, магазин (на счёте 1000 монет), рейтинг, достижения;\n"
    "👩‍🏫 Учитель — свои квесты, проверка ответов, монеты классу;\n"
    "🛡 Модератор — помощник админа без денежных операций;\n"
    "🛠 Админ — вся школа: классы, учителя, награды, оповещения, статистика, сезоны."
)


class DemoMixin:
    demo: bool

    async def _demo_welcome(self, session: AsyncSession, user: User) -> Screen:
        """Первое сообщение нового пользователя: сразу админ, без регистрации."""
        await demo_service.switch_role(session, user, Role.admin)
        return self._menu(user, WELCOME)

    def _demo_screen(self, user: User) -> Screen:
        rows = [
            [callback_button(a, f"demo:role:{ra.value}"), callback_button(b, f"demo:role:{rb.value}")]
            for (ra, a), (rb, b) in zip(DEMO_ROLES[::2], DEMO_ROLES[1::2])
        ]
        return f"{ROLES_HELP}\n\nСейчас вы: {ROLE_TITLES[user.role]}.", keyboard(rows)

    async def _demo_callback(self, session: AsyncSession, user: User, payload: str) -> Screen | str:
        try:
            role = Role(payload.removeprefix("demo:role:"))
        except ValueError:
            return "Неизвестная роль."
        await demo_service.switch_role(session, user, role)
        return self._menu(user, f"🧪 Теперь вы — {ROLE_TITLES[role]}.")
