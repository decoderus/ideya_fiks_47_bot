"""Журнал успехов, пульс класса и выгрузка в таблицу."""
from app.db.models import SchoolClass
from app.keyboards.base import LIST_LIMIT, _class_grid, _grid, _pages, _short, keyboard
from app.max_api import callback_button

JOURNAL_PAGE = 10


SUCCESS_PAGE = 8


def journal_hub(classes: list[SchoolClass], can_write: bool, can_export: bool = False) -> list[dict]:
    """«📒 Журнал успехов»: отметить успех, последние записи, классы."""
    rows = [[callback_button("✍️ Отметить успех", "jr:new")]] if can_write else []
    rows.append([callback_button("🕘 Последние записи", "jr:feed:0")])
    if can_export:
        rows.append([callback_button("📥 Вся школа в таблицу", "jr:xls:0")])
    rows += _class_grid(classes, "jr:cls:{id}:0")
    rows.append([callback_button("« В меню", "menu:main")])
    return keyboard(rows)


def journal_students(
    students: list,
    successes: dict[int, int],
    done: dict[int, int],
    class_id: int,
    page: int,
    has_more: bool,
) -> list[dict]:
    rows = [
        [
            callback_button(
                f"{_short(s.display_name, 24)} · 🌟{successes.get(s.id, 0)} · 🎯{done.get(s.id, 0)}",
                f"jr:st:{s.id}:0",
            )
        ]
        for s in students
    ]
    rows += _pages(f"jr:all:{class_id}:", page, has_more)
    rows.append([_to_pulse(class_id)])
    return keyboard(rows)


def _to_pulse(class_id: int) -> dict:
    return callback_button("« Пульс класса", f"jr:cls:{class_id}:0")


def class_pulse(
    class_id: int, inactive: int, need_help: int, can_write: bool, can_export: bool = False
) -> list[dict]:
    """«Пульс класса»: списки, где учителю действовать, и все ученики."""
    lists = []
    if inactive:
        lists.append(callback_button(f"😴 Неактивные · {inactive}", f"jr:idle:{class_id}"))
    if need_help:
        lists.append(callback_button(f"⚠️ Нужна помощь · {need_help}", f"jr:help:{class_id}"))
    rows = [lists] if lists else []
    rows.append([callback_button("👥 Все ученики", f"jr:all:{class_id}:0")])
    if can_write:
        rows.append([callback_button("✍️ Отметить успех", f"jr:nc:{class_id}:0")])
    if can_export:
        rows.append([callback_button("📥 Выгрузить в таблицу", f"jr:xls:{class_id}")])
    rows.append([callback_button("« К классам", "menu:journal")])
    return keyboard(rows)


def pulse_students(items: list[tuple], class_id: int) -> list[dict]:
    """items: (ученик, подпись) — в карточку ученика."""
    rows = [
        [callback_button(f"{_short(s.display_name, 24)} · {_short(note, 22)}", f"jr:st:{s.id}:0")]
        for s, note in items[:LIST_LIMIT]
    ]
    rows.append([_to_pulse(class_id)])
    return keyboard(rows)


def _success_button(success, prefix_date) -> dict:
    from app.services.successes import BY_KEY

    cat = BY_KEY.get(success.category, BY_KEY["other"])
    return callback_button(
        f"{cat.emoji} {prefix_date(success.created_at)} · {_short(success.text, 28)}",
        f"jr:e:{success.id}",
    )


def journal_card(student, items: list, page: int, has_more: bool, can_write: bool, date_of) -> list[dict]:
    rows = [[callback_button("✍️ Отметить успех", f"jr:ns:{student.id}")]] if can_write else []
    rows += [[_success_button(s, date_of)] for s in items]
    rows += _pages(f"jr:st:{student.id}:", page, has_more)
    rows.append([callback_button("« К классу", f"jr:cls:{student.class_id}:0")])
    return keyboard(rows)


def success_entry(success, can_delete: bool) -> list[dict]:
    rows = [[callback_button("🗑 Удалить запись", f"jr:del:{success.id}")]] if can_delete else []
    rows.append([callback_button("« К ученику", f"jr:st:{success.student_id}:0")])
    return keyboard(rows)


def success_delete_confirm(success_id: int) -> list[dict]:
    return keyboard(
        [
            [
                callback_button("🗑 Да, удалить", f"jr:delok:{success_id}"),
                callback_button("Отмена", f"jr:e:{success_id}"),
            ]
        ]
    )


def success_feed(items: list, page: int, has_more: bool, date_of) -> list[dict]:
    rows = [[_success_button(s, date_of)] for s in items]
    rows += _pages("jr:feed:", page, has_more)
    rows.append([callback_button("« Журнал успехов", "menu:journal")])
    return keyboard(rows)


def success_write_classes(classes: list[SchoolClass]) -> list[dict]:
    rows = _class_grid(classes, "jr:nc:{id}:0")
    rows.append([callback_button("✖️ Отмена", "cancel")])
    return keyboard(rows)


def success_write_students(students: list, class_id: int, page: int, has_more: bool) -> list[dict]:
    rows = [[callback_button(_short(s.display_name, 36), f"jr:ns:{s.id}")] for s in students]
    rows += _pages(f"jr:nc:{class_id}:", page, has_more)
    rows.append([callback_button("« К классам", "jr:new"), callback_button("✖️ Отмена", "cancel")])
    return keyboard(rows)


def success_categories() -> list[dict]:
    from app.services.successes import CATEGORIES

    buttons = [callback_button(f"{c.emoji} {c.title}", f"jr:cat:{c.key}") for c in CATEGORIES]
    rows = _grid(buttons, 2)
    rows.append([callback_button("✖️ Отмена", "cancel")])
    return keyboard(rows)


def success_coins(choices: list[int]) -> list[dict]:
    rows = [[callback_button("Без монет", "jr:coins:0")]]
    rows.append([callback_button(f"+{c}", f"jr:coins:{c}") for c in choices])
    rows.append([callback_button("✖️ Отмена", "cancel")])
    return keyboard(rows)


def success_saved(student_id: int) -> list[dict]:
    return keyboard(
        [
            [callback_button("👤 Карточка ученика", f"jr:st:{student_id}:0")],
            [callback_button("✍️ Отметить ещё", "jr:new")],
        ]
    )


def my_successes(page: int, has_more: bool) -> list[dict]:
    rows = _pages("jr:my:", page, has_more)
    rows.append([callback_button("« В профиль", "menu:profile")])
    return keyboard(rows)


def success_notice() -> list[dict]:
    return keyboard([[callback_button("🌟 Мои успехи", "jr:my:0")]])


def export_periods(class_id: int) -> list[dict]:
    from app.services.export import PERIODS

    back = f"jr:cls:{class_id}:0" if class_id else "menu:journal"
    return keyboard([
        [callback_button(title.capitalize(), f"jr:xp:{class_id}:{days}") for days, title in PERIODS.items()],
        [callback_button("« Назад", back)],
    ])


def export_done(back: str) -> list[dict]:
    return keyboard([[callback_button("« Назад", back)]])
