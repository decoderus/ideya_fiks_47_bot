"""Оповещения: рассылка, выбор получателей, история, входящие ученика."""
from app.db.models import SchoolClass
from app.keyboards.base import _class_grid, _short, keyboard
from app.max_api import callback_button

BROADCAST_PAGE = 10


def broadcast_menu(draft: bool = False) -> list[dict]:
    title = "✍️ Подготовить оповещение" if draft else "✍️ Новое оповещение"
    return keyboard(
        [
            [callback_button(title, "bc:new")],
            [callback_button("🗂 Отправленные", "bc:history:0")],
            [callback_button("« В меню", "menu:main")],
        ]
    )


def broadcast_confirm(draft: bool = False) -> list[dict]:
    title = "📨 Отправить админу на подтверждение" if draft else "📤 Отправить"
    return keyboard(
        [
            [callback_button(title, "bc:send")],
            [callback_button("✖️ Отмена", "cancel")],
        ]
    )


def broadcast_history(items, page: int, has_more: bool) -> list[dict]:
    rows = []
    for b in items:
        preview = b.text.replace("\n", " ") if b.text else "📷 фото"
        rows.append([callback_button(_short(preview), f"bc:open:{b.id}")])
    nav = []
    if page > 0:
        nav.append(callback_button("« Новее", f"bc:history:{page - 1}"))
    if has_more:
        nav.append(callback_button("Старше »", f"bc:history:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([callback_button("📣 К оповещениям", "menu:broadcast")])
    return keyboard(rows)


def broadcast_read(broadcast_id: int) -> list[dict]:
    return keyboard([[callback_button("✅ Прочитано", f"bc:read:{broadcast_id}")]])


INBOX_PAGE = 10


def inbox_list(items, page: int, has_more: bool, date_of) -> list[dict]:
    """items: BroadcastRecipient; date_of: форматирование даты оповещения."""
    rows = []
    for r in items:
        b = r.broadcast
        mark = "🔵 " if r.read_at is None else ""
        preview = b.text.replace("\n", " ") if b.text else "📷 фото"
        text = f"{mark}{date_of(b.created_at)} · {_short(preview, 30)}"
        rows.append([callback_button(text, f"inbox:open:{b.id}")])
    nav = []
    if page > 0:
        nav.append(callback_button("« Новее", f"inbox:{page - 1}"))
    if has_more:
        nav.append(callback_button("Старше »", f"inbox:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([callback_button("« В меню", "menu:main")])
    return keyboard(rows)


def to_inbox() -> list[dict]:
    return keyboard(
        [
            [callback_button("📣 К оповещениям", "inbox:0")],
            [callback_button("« В меню", "menu:main")],
        ]
    )


def to_broadcasts() -> list[dict]:
    return keyboard([[callback_button("📣 К оповещениям", "menu:broadcast")]])


def broadcast_audience() -> list[dict]:
    """Шаг 2 оповещения: кому отправить."""
    return keyboard(
        [
            [callback_button("🏫 Всем: ученики и учителя", "bc:aud:all")],
            [
                callback_button("🎓 Все ученики", "bc:aud:students"),
                callback_button("👩‍🏫 Учителя", "bc:aud:teachers"),
            ],
            [callback_button("🛡 Модераторы", "bc:aud:moderators")],
            [callback_button("👥 Ученики одного класса", "bc:aud:cls")],
            [callback_button("👤 Одному ученику", "bc:aud:pick")],
            [
                callback_button("👤 Одному учителю", "bc:aud:pr:teacher:0"),
                callback_button("👤 Одному модератору", "bc:aud:pr:moderator:0"),
            ],
            [callback_button("✖️ Отмена", "cancel")],
        ]
    )


def broadcast_classes(classes: list[SchoolClass], prefix: str) -> list[dict]:
    """Выбор класса: prefix bc:aud:class (весь класс) или bc:aud:pc (затем ученик)."""
    rows = _class_grid(classes, prefix + ":{id}")
    rows.append([callback_button("« Назад", "bc:aud:back")])
    return keyboard(rows)


STUDENT_PAGE = 20


def broadcast_people(
    people: list, page: int, has_more: bool, pages: str, back: tuple[str, str]
) -> list[dict]:
    """Выбор адресата. pages — payload страниц без номера, back — (подпись, payload)."""
    rows = [[callback_button(_short(p.display_name, 40), f"bc:aud:user:{p.id}")] for p in people]
    nav = []
    if page > 0:
        nav.append(callback_button("« Назад", f"{pages}:{page - 1}"))
    if has_more:
        nav.append(callback_button("Дальше »", f"{pages}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([callback_button(*back)])
    return keyboard(rows)
