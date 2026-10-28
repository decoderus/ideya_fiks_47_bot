"""Вопросы учеников по квестам."""
from app.keyboards.base import _short, keyboard
from app.max_api import callback_button


def question_notice(question_id: int, clarify: bool = False) -> list[dict]:
    label = "✍️ Уточнить ответ" if clarify else "✍️ Ответить"
    return keyboard([[callback_button(label, f"qa:ans:{question_id}")]])


def question_list(items: list) -> list[dict]:
    rows = []
    for q in items:
        mark = "✅" if q.answers else "🆕"
        topic = q.quest.title if q.quest else "Общий вопрос"
        text = f"{mark} {_short(topic, 22)} · {_short(q.student.display_name, 18)}"
        rows.append([callback_button(text, f"qa:open:{q.id}")])
    rows.append([callback_button("« В меню", "menu:main")])
    return keyboard(rows)


def question_card(question_id: int, action: str | None) -> list[dict]:
    rows = [[callback_button(action, f"qa:ans:{question_id}")]] if action else []
    rows.append([callback_button("« К вопросам", "menu:questions")])
    return keyboard(rows)
