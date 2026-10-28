"""Школа (админ): классы, ученики, учителя, журнал регистраций, статистика, журналы действий."""
from app.db.models import SchoolClass
from app.keyboards.base import _grid, _short, keyboard, locked, with_count
from app.max_api import callback_button

CLASS_STUDENTS_PAGE = 10


def school_menu() -> list[dict]:
    """«🏫 Школа»: классы и учителя (админ и модератор)."""
    return keyboard(
        [
            [callback_button("🏫 Классы", "menu:classes")],
            [callback_button("👩‍🏫 Учителя", "menu:teachers")],
            [callback_button("« В меню", "menu:main")],
        ]
    )


def _back_to_school() -> dict:
    return callback_button("« Школа", "menu:school")


def classes_overview(
    classes: list[SchoolClass], admin: bool = True, show_locked: bool = False, staff: bool = True
) -> list[dict]:
    """staff — админ или модератор: назад в «Школу»; учитель — в меню."""
    if admin:
        rows = [[callback_button("➕ Добавить классы", "cls:add")]]
    else:
        rows = [[locked("Добавить классы")]] if show_locked else []
    rows += _grid([callback_button(c.name, f"cls:open:{c.id}") for c in classes])
    rows.append([_back_to_school() if staff else callback_button("« В меню", "menu:main")])
    return keyboard(rows)


def class_card(class_id: int, admin: bool = True, show_locked: bool = False) -> list[dict]:
    if show_locked:
        return keyboard(
            [
                [callback_button("👥 Ученики", f"cls:st:{class_id}:0"), locked("Переименовать")],
                [locked("Удалить класс")],
                [callback_button("« К классам", "menu:classes")],
            ]
        )
    if not admin:
        return keyboard(
            [
                [callback_button("👥 Ученики", f"cls:st:{class_id}:0")],
                [callback_button("« К классам", "menu:classes")],
            ]
        )
    return keyboard(
        [
            [
                callback_button("👥 Ученики", f"cls:st:{class_id}:0"),
                callback_button("✏️ Переименовать", f"cls:ren:{class_id}"),
            ],
            [callback_button("🗑 Удалить класс", f"cls:rm:{class_id}")],
            [callback_button("« К классам", "menu:classes")],
        ]
    )


def class_delete_confirm(class_id: int) -> list[dict]:
    return keyboard(
        [
            [
                callback_button("🗑 Да, удалить", f"cls:rm_ok:{class_id}"),
                callback_button("Нет", f"cls:open:{class_id}"),
            ]
        ]
    )


def class_students(students: list, class_id: int, page: int, has_more: bool) -> list[dict]:
    rows = [
        [callback_button(f"{_short(s.display_name, 30)} · {s.balance}", f"cls:stu:{s.id}")]
        for s in students
    ]
    nav = []
    if page > 0:
        nav.append(callback_button("« Назад", f"cls:st:{class_id}:{page - 1}"))
    if has_more:
        nav.append(callback_button("Дальше »", f"cls:st:{class_id}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([callback_button("« К классу", f"cls:open:{class_id}")])
    return keyboard(rows)


def student_admin_card(
    student, can_transfer: bool = True, can_delete: bool = True, show_locked: bool = False
) -> list[dict]:
    back = f"cls:st:{student.class_id}:0" if student.class_id else "menu:classes"
    rows = [[callback_button("📜 История баланса", f"cls:hist:{student.id}:0")]]
    actions = []
    if can_transfer:
        actions.append(callback_button("🔁 Перевести", f"cls:mv:{student.id}"))
    if can_delete:
        actions.append(callback_button("🗑 Удалить", f"cls:del:{student.id}"))
    elif show_locked:
        actions.append(locked("Удалить"))
    if actions:
        rows.append(actions)
    rows.append([callback_button("« К ученикам", back)])
    return keyboard(rows)


BALANCE_PAGE = 15


def balance_history(student_id: int, page: int, has_more: bool) -> list[dict]:
    nav = []
    if page > 0:
        nav.append(callback_button("« Новее", f"cls:hist:{student_id}:{page - 1}"))
    if has_more:
        nav.append(callback_button("Старше »", f"cls:hist:{student_id}:{page + 1}"))
    rows = [nav] if nav else []
    rows.append([callback_button("« К ученику", f"cls:stu:{student_id}")])
    return keyboard(rows)


def transfer_picker(student_id: int, classes: list[SchoolClass], current_id: int | None) -> list[dict]:
    rows = _grid(
        [
            callback_button(c.name, f"cls:mvto:{student_id}:{c.id}")
            for c in classes
            if c.id != current_id
        ]
    )
    rows.append([callback_button("✖️ Отмена", f"cls:stu:{student_id}")])
    return keyboard(rows)


def student_delete_confirm(student_id: int) -> list[dict]:
    return keyboard(
        [
            [
                callback_button("🗑 Да, удалить", f"cls:del_ok:{student_id}"),
                callback_button("Нет", f"cls:stu:{student_id}"),
            ]
        ]
    )


def to_classes() -> list[dict]:
    return keyboard([[callback_button("« К классам", "menu:classes")]])


def teachers_overview(requests: list, teachers: list) -> list[dict]:
    rows = [[callback_button(f"⏳ {_short(r.full_name, 30)}", f"tr:open:{r.id}")] for r in requests]
    for t in teachers:
        names = ", ".join(c.name for c in t.teacher_classes) or "без классов"
        rows.append([callback_button(f"{_short(t.display_name, 24)} · {_short(names, 20)}", f"tc:open:{t.id}")])
    rows.append([_back_to_school()])
    return keyboard(rows)


def teacher_request_actions(request_id: int) -> list[dict]:
    return keyboard(
        [
            [
                callback_button("✅ Подтвердить", f"tr:ok:{request_id}"),
                callback_button("❌ Отклонить", f"tr:no:{request_id}"),
            ],
            [callback_button("✏️ Изменить классы", f"tr:edit:{request_id}")],
        ]
    )


def teacher_request_card(request_id: int) -> list[dict]:
    return keyboard(
        [
            [
                callback_button("✅ Подтвердить", f"tr:ok:{request_id}"),
                callback_button("❌ Отклонить", f"tr:no:{request_id}"),
            ],
            [callback_button("✏️ Изменить классы", f"tr:edit:{request_id}")],
            [callback_button("« К учителям", "menu:teachers")],
        ]
    )


def request_classes_editor(request_id: int, classes: list[SchoolClass], chosen: set[int]) -> list[dict]:
    """Проверяющий правит классы в заявке учителя."""
    rows = _grid(
        [
            callback_button(f"{'✅ ' if c.id in chosen else ''}{c.name}", f"tr:t:{request_id}:{c.id}")
            for c in classes
        ]
    )
    rows.append([callback_button("✅ Подтвердить с этими классами", f"tr:ok:{request_id}")])
    rows.append([callback_button("« К заявке", f"tr:open:{request_id}")])
    return keyboard(rows)


def reg_teacher_classes(classes: list[SchoolClass], chosen: set[int]) -> list[dict]:
    """Учитель при регистрации отмечает классы, в которых работает."""
    rows = _grid(
        [
            callback_button(f"{'✅ ' if c.id in chosen else ''}{c.name}", f"reg:tcl:{c.id}")
            for c in classes
        ]
    )
    rows.append([callback_button("📨 Отправить заявку", "reg:tcl_done")])
    rows.append([callback_button("« Назад", "reg:back")])
    return keyboard(rows)


def teacher_classes_editor(teacher_id: int, classes: list[SchoolClass], assigned: set[int]) -> list[dict]:
    rows = _grid(
        [
            callback_button(f"{'✅ ' if c.id in assigned else ''}{c.name}", f"tc:t:{teacher_id}:{c.id}")
            for c in classes
        ]
    )
    rows.append([callback_button("✔️ Готово", f"tc:done:{teacher_id}")])
    return keyboard(rows)


def to_teachers() -> list[dict]:
    return keyboard([[callback_button("« К учителям", "menu:teachers")]])


ENROLLMENT_PAGE = 10


def enrollment_list(items: list, page: int, has_more: bool, any_unchecked: bool, date_of) -> list[dict]:
    rows = []
    if any_unchecked:
        rows.append([callback_button("✅ Отметить все проверенными", "enl:all")])
    for e in items:
        mark = "🔵 " if e.checked_at is None else ""
        text = f"{mark}{date_of(e.created_at)} · {_short(e.full_name, 24)} · {e.class_name}"
        rows.append([callback_button(text, f"enl:open:{e.id}")])
    nav = []
    if page > 0:
        nav.append(callback_button("« Новее", f"enl:{page - 1}"))
    if has_more:
        nav.append(callback_button("Старше »", f"enl:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([callback_button("📊 К статистике", "menu:stats")])
    return keyboard(rows)


def to_enrollments() -> list[dict]:
    return keyboard([[callback_button("🆕 К регистрациям", "enl:0")]])


def stats(admin: bool = True, unchecked: int = 0) -> list[dict]:
    """unchecked — регистраций учеников, которые ещё не проверены."""
    registrations = [callback_button(with_count("🆕 Регистрации", unchecked), "enl:0")]
    if admin:
        logs = [
            callback_button("💰 Движение монет", "log:coins:0"),
            callback_button("🛡 Действия персонала", "log:staff:0"),
        ]
    else:
        logs = [locked("Движение монет"), locked("Действия персонала")]
    return keyboard(
        [
            registrations,
            logs,
            *([[callback_button("📋 Опрос учеников и учителей", "sv:admin")]] if admin else []),
            *([[callback_button("📥 Выгрузка в таблицу", "jr:xls:0")]] if admin else []),
            [callback_button("🔄 Обновить", "menu:stats")],
            [callback_button("« В меню", "menu:main")],
        ]
    )


LOG_PAGE = 15


def log_nav(kind: str, page: int, has_more: bool) -> list[dict]:
    nav = []
    if page > 0:
        nav.append(callback_button("« Новее", f"log:{kind}:{page - 1}"))
    if has_more:
        nav.append(callback_button("Старше »", f"log:{kind}:{page + 1}"))
    rows = [nav] if nav else []
    rows.append([callback_button("📊 К статистике", "menu:stats")])
    return keyboard(rows)


def teacher_approval_undo(request_id: int) -> list[dict]:
    """Админу: модератор подтвердил учителя — можно отменить."""
    return keyboard([[callback_button("↩️ Отменить подтверждение", f"tr:undo:{request_id}")]])
