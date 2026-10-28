"""Профиль ученика и напоминания."""
from app.db.models import Quest
from app.keyboards.base import _short, keyboard
from app.max_api import callback_button


def profile_actions(reminders_off: bool = False) -> list[dict]:
    toggle = (
        callback_button("🔔 Включить напоминания", "rem:on")
        if reminders_off
        else callback_button("🔕 Отключить напоминания", "rem:off")
    )
    return keyboard(
        [
            [
                callback_button("🏅 Достижения", "menu:achievements"),
                callback_button("🏆 Рейтинг", "menu:rating"),
            ],
            [callback_button("🎯 Квесты", "menu:quests"), callback_button("🌟 Мои успехи", "jr:my:0")],
            [toggle],
            [callback_button("« В меню", "menu:main")],
        ]
    )


def reminder_actions(quests: list[Quest]) -> list[dict]:
    rows = [[callback_button(f"🎯 {_short(q.title)}", f"q:open:{q.id}")] for q in quests[:5]]
    rows.append([callback_button("👤 Профиль", "menu:profile")])
    rows.append([callback_button("🔕 Не напоминать", "rem:off")])
    return keyboard(rows)
