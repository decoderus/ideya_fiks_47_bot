"""Рейтинг и соревнование классов."""
from app.db.models import SchoolClass
from app.keyboards.base import _short, keyboard
from app.max_api import callback_button


def _badge_icons(badges: dict[int, list[str]], class_id: int) -> str:
    return "".join(f" {b.split(' ', 1)[0]}" for b in badges.get(class_id, []))


def _mark(text: str, active: bool) -> str:
    return f"• {text} •" if active else text


def rating_nav(
    classes: list[SchoolClass],
    current: int | None,
    badges: dict[int, list[str]] | None = None,
    by_classes: bool = True,
) -> list[dict]:
    """current: id класса или None для рейтинга школы; badges — значки классов-победителей.

    by_classes: кнопка «По классам» со списком всех классов (персонал);
    иначе — кнопки самих классов (ученику — только его класс).
    """
    badges = badges or {}
    rows = [[callback_button(_mark("🏫 Школа", current is None), "rate:school")]]
    if by_classes:
        chosen = next((c for c in classes if c.id == current), None)
        title = f"👥 По классам · {_short(chosen.name, 16)}" if chosen else "👥 По классам"
        rows.append([callback_button(_mark(title, chosen is not None), "rate:classes")])
    else:
        for c in classes:
            title = f"👥 {_short(c.name)}{_badge_icons(badges, c.id)}"
            rows.append([callback_button(_mark(title, c.id == current), f"rate:class:{c.id}")])
    rows.append([callback_button("🏁 Соревнование классов", "rate:contest")])
    rows.append([callback_button("📜 Прошлые сезоны", "sz:archive")])
    rows.append([callback_button("« В меню", "menu:main")])
    return keyboard(rows)


def rating_classes(
    classes: list[SchoolClass], badges: dict[int, list[str]] | None = None
) -> list[dict]:
    """Все классы для рейтинга — по три в ряд."""
    badges = badges or {}
    rows, row = [], []
    for c in classes:
        row.append(callback_button(f"{_short(c.name, 14)}{_badge_icons(badges, c.id)}", f"rate:class:{c.id}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([callback_button("« К рейтингу школы", "rate:school")])
    return keyboard(rows)


def contest_nav() -> list[dict]:
    return keyboard(
        [
            [callback_button("🏆 Рейтинг школы", "rate:school")],
            [callback_button("« В меню", "menu:main")],
        ]
    )


def contest_notice() -> list[dict]:
    return keyboard([[callback_button("🏁 Соревнование классов", "rate:contest")]])
