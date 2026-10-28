"""Монеты вручную: выбор класса и ученика, подтверждение."""
from app.db.models import SchoolClass
from app.keyboards.base import _class_grid, _short, keyboard
from app.max_api import callback_button

COINS_PAGE = 10


def coins_classes(classes: list[SchoolClass]) -> list[dict]:
    rows = _class_grid(classes, "coins:cls:{id}:0")
    rows.append([callback_button("« В меню", "menu:main")])
    return keyboard(rows)


def coins_students(
    students: list, class_id: int, page: int, has_more: bool, to_classes: bool
) -> list[dict]:
    rows = []
    if students:
        rows.append([callback_button("👥 Начислить всему классу", f"coins:all:{class_id}")])
    rows += [
        [callback_button(f"{_short(s.display_name, 30)} · {s.balance}", f"coins:st:{s.id}")]
        for s in students
    ]
    nav = []
    if page > 0:
        nav.append(callback_button("« Назад", f"coins:cls:{class_id}:{page - 1}"))
    if has_more:
        nav.append(callback_button("Дальше »", f"coins:cls:{class_id}:{page + 1}"))
    if nav:
        rows.append(nav)
    if to_classes:
        rows.append([callback_button("« К классам", "menu:coins")])
    rows.append([callback_button("« В меню", "menu:main")])
    return keyboard(rows)


def coins_card(student) -> list[dict]:
    back = f"coins:cls:{student.class_id}:0" if student.class_id else "menu:coins"
    return keyboard(
        [
            [
                callback_button("➕ Начислить", f"coins:give:{student.id}"),
                callback_button("➖ Списать", f"coins:take:{student.id}"),
            ],
            [callback_button("« К списку", back)],
        ]
    )


def coins_confirm() -> list[dict]:
    return keyboard(
        [
            [callback_button("✅ Подтвердить", "coins:ok")],
            [callback_button("✖️ Отмена", "cancel")],
        ]
    )
