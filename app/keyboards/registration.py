"""Регистрация и проверка зачисления ученика."""
from app.db.models import SchoolClass
from app.keyboards.base import _grid, keyboard
from app.max_api import callback_button


def reg_roles(selected: str) -> list[dict]:
    def option(title: str, role: str) -> dict:
        mark = "✅ " if role == selected else ""
        return callback_button(f"{mark}{title}", f"reg:role:{role}")

    return keyboard(
        [
            [option("🎒 Ученик", "student"), option("👩‍🏫 Учитель", "teacher")],
            [callback_button("Продолжить ▶", "reg:next")],
        ]
    )


def reg_teacher_confirm() -> list[dict]:
    return keyboard(
        [
            [callback_button("📨 Подать заявку", "reg:teacher_ok")],
            [callback_button("« Назад", "reg:back")],
        ]
    )


def reg_back() -> list[dict]:
    return keyboard([[callback_button("« Назад", "reg:back")]])


def reg_categories(categories: list[tuple[str, str]]) -> list[dict]:
    rows = [[callback_button(title, f"reg:cat:{key}")] for key, title in categories]
    rows.append([callback_button("« Назад", "reg:back")])
    return keyboard(rows)


def reg_classes(classes: list[SchoolClass]) -> list[dict]:
    rows = _grid([callback_button(c.name, f"reg:class:{c.id}") for c in classes])
    rows.append([callback_button("« К группам классов", "reg:cats")])
    return keyboard(rows)


def reg_retry() -> list[dict]:
    return keyboard(
        [[callback_button("🔄 Проверить снова", "reg:cats")], [callback_button("« Назад", "reg:back")]]
    )


def reg_waiting() -> list[dict]:
    return keyboard([[callback_button("↩️ Отозвать заявку", "reg:withdraw")]])


def reg_restart() -> list[dict]:
    return keyboard([[callback_button("📝 Зарегистрироваться заново", "reg:restart")]])


def student_admin_actions(student_id: int) -> list[dict]:
    return keyboard(
        [
            [
                callback_button("🔁 Перевести", f"cls:mv:{student_id}"),
                callback_button("🗑 Удалить", f"cls:del:{student_id}"),
            ]
        ]
    )


def enrollment_teacher_actions(student_id: int, class_id: int) -> list[dict]:
    return keyboard(
        [
            [callback_button("🗑 Удалить ученика", f"enr:del:{student_id}:{class_id}")],
            [callback_button("🔁 Нужен перевод в другой класс", f"enr:mv:{student_id}:{class_id}")],
        ]
    )


def enrollment_delete_confirm(student_id: int, class_id: int) -> list[dict]:
    return keyboard(
        [
            [
                callback_button("🗑 Да, удалить", f"enr:del_ok:{student_id}:{class_id}"),
                callback_button("Нет", "menu:main"),
            ]
        ]
    )
