"""Достижения: каталог, карточка, вручение и отзыв."""
from app.db.models import AchievementKind, SchoolClass
from app.keyboards.base import _grid, _short, keyboard, locked
from app.max_api import callback_button

ACH_STUDENTS_PAGE = 10


def to_achievements() -> list[dict]:
    return keyboard([[callback_button("🏅 Мои достижения", "menu:achievements")]])


def achievements_catalog(items: list, holders: dict[int, int], readonly: bool = False) -> list[dict]:
    rows = [[locked("Новое достижение")]] if readonly else [
        [callback_button("➕ Новое достижение", "ach:new")]
    ]
    for a in items:
        rows.append(
            [callback_button(f"{a.emoji} {_short(a.title, 30)} · {holders.get(a.id, 0)}", f"ach:open:{a.id}")]
        )
    rows.append([callback_button("« В меню", "menu:main")])
    return keyboard(rows)


def achievement_card(a, readonly: bool = False) -> list[dict]:
    rows = []
    if a.kind == AchievementKind.manual:
        grant = locked("Выдать ученику") if readonly else callback_button(
            "🎖 Выдать ученику", f"ach:gcls:{a.id}"
        )
        rows.append([grant])
    rows.append(
        [locked("Изменить бонус") if readonly else callback_button("💰 Изменить бонус", f"ach:reward:{a.id}")]
    )
    rows.append([callback_button("« К достижениям", "ach:list")])
    return keyboard(rows)


def achievement_classes(achievement_id: int, classes: list[SchoolClass]) -> list[dict]:
    rows = _grid([callback_button(c.name, f"ach:gst:{achievement_id}:{c.id}:0") for c in classes])
    rows.append([callback_button("« К достижению", f"ach:open:{achievement_id}")])
    return keyboard(rows)


def achievement_students(
    achievement_id: int, class_id: int, students: list, owners: set[int], page: int, has_more: bool
) -> list[dict]:
    rows = [
        [
            callback_button(
                f"{'✅ ' if s.id in owners else ''}{_short(s.display_name, 30)}",
                f"ach:give:{achievement_id}:{s.id}",
            )
        ]
        for s in students
    ]
    nav = []
    if page > 0:
        nav.append(callback_button("« Назад", f"ach:gst:{achievement_id}:{class_id}:{page - 1}"))
    if has_more:
        nav.append(callback_button("Дальше »", f"ach:gst:{achievement_id}:{class_id}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([callback_button("« К классам", f"ach:gcls:{achievement_id}")])
    return keyboard(rows)


def achievement_revoke_confirm(achievement_id: int, student_id: int, class_id: int) -> list[dict]:
    return keyboard(
        [
            [
                callback_button("↩️ Отозвать", f"ach:rev_ok:{achievement_id}:{student_id}"),
                callback_button("Нет", f"ach:gst:{achievement_id}:{class_id}:0"),
            ]
        ]
    )


def achievement_emoji_choice() -> list[dict]:
    return keyboard(
        [
            [callback_button("🏅 Оставить стандартный", "ach:emoji:default")],
            [callback_button("✖️ Отмена", "cancel")],
        ]
    )
