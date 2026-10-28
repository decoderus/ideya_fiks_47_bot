"""Общее для всех экранов: клавиатура с «🏠 В меню», главное меню по ролям, подписи, монеты."""
from app.db.models import QuestKind, QuestRepeat, Role, SchoolClass, SubmissionStatus
from app.max_api import callback_button, link_button
from app.max_api import keyboard as _keyboard

HOME = "🏠 В меню"


def keyboard(rows: list[list[dict]]) -> list[dict]:
    """Клавиатура экрана. Если на нём нет пути в главное меню, добавляет «🏠 В меню»:
    с любого экрана — в меню одним нажатием, в том числе посреди проверки или выбора
    (нажатие работает как отмена). Регистрацию не трогает: у незарегистрированного
    меню ещё нет. Само главное меню строится без неё (menu_for)."""
    buttons = [b for row in rows for b in row]
    payloads = [b.get("payload", "") for b in buttons]  # у кнопки-ссылки payload нет
    has_exit = "menu:main" in payloads or "cancel" in payloads  # «Отмена» тоже ведёт в меню
    if not has_exit and not any(p.startswith("reg:") for p in payloads):
        home = callback_button(HOME, "menu:main")
        last = rows[-1] if rows else []
        if len(last) == 1 and last[0]["text"].startswith("«"):
            rows = rows[:-1] + [last + [home]]  # рядом с «назад», если он один в ряду
        else:
            rows = rows + [[home]]
    return _keyboard(rows)


# Кнопка с замком: модератор видит возможность админа, но нажатие только
# показывает «недоступно» (сделать кнопку в MAX некликабельной нельзя)
LOCKED = "locked"


def locked(text: str) -> dict:
    return callback_button(f"🔒 {text}", LOCKED)


# Разделы меню по ролям. Позже сюда добавляются новые модули.
MENUS: dict[Role, list[tuple[str, str]]] = {
    Role.student: [
        ("👤 Профиль", "menu:profile"),
        ("🎯 Квесты", "menu:quests"),
        ("🛒 Магазин", "menu:shop"),
        ("🏆 Рейтинг", "menu:rating"),
        ("📣 Оповещения", "inbox:0"),
        ("🏅 Достижения", "menu:achievements"),
        ("❓ Задать вопрос", "qa:ask:0"),
        ("🔄 Перезапустить", "menu:restart"),
    ],
    Role.teacher: [
        ("📂 Квесты", "mq:list:a:0"),
        ("📋 На проверке", "menu:review"),
        ("❓ Вопросы", "menu:questions"),
        ("📦 Выдача", "ord:list"),
        ("💰 Монеты", "menu:coins"),
        ("🏆 Рейтинг", "menu:rating"),
        ("📒 Журнал успехов", "menu:journal"),
        ("🏫 Мои классы", "menu:classes"),
        ("🔄 Перезапустить", "menu:restart"),
    ],
    # Выдача — в «Наградах», регистрации — в «Статистике», классы и учителя — в «Школе»
    Role.admin: [
        ("📂 Квесты", "mq:list:a:0"),
        ("📋 На проверке", "menu:review"),
        ("❓ Вопросы", "menu:questions"),
        ("💰 Монеты", "menu:coins"),
        ("🏆 Рейтинг", "menu:rating"),
        ("🎁 Награды", "menu:rewards"),
        ("📣 Оповещения", "menu:broadcast"),
        ("🏫 Школа", "menu:school"),
        ("📊 Статистика", "menu:stats"),
        ("📒 Журнал успехов", "menu:journal"),
        ("🏅 Достижения", "menu:achievements"),
        ("📅 Сезоны", "menu:seasons"),
        ("🔄 Перезапустить", "menu:restart"),
    ],
    # Как у админа; недоступное — с замком. Награды — только просмотр,
    # оповещения — на подтверждение админу
    Role.moderator: [
        ("📂 Квесты", "mq:list:a:0"),
        ("🔒 На проверке", LOCKED),
        ("❓ Вопросы", "menu:questions"),
        ("🔒 Монеты", LOCKED),
        ("🏆 Рейтинг", "menu:rating"),
        ("🎁 Награды", "menu:rewards"),
        ("📣 Оповещения", "menu:broadcast"),
        ("🏫 Школа", "menu:school"),
        ("📊 Статистика", "menu:stats"),
        ("📒 Журнал успехов", "menu:journal"),
        ("🏅 Достижения", "menu:achievements"),
        ("📅 Сезоны", "menu:seasons"),
        ("🔄 Перезапустить", "menu:restart"),
    ],
}


ROLE_TITLES = {
    Role.student: "ученик",
    Role.teacher: "учитель",
    Role.moderator: "модератор",
    Role.admin: "админ",
}


KIND_TITLES = {
    QuestKind.auto: "✅ Автоподтверждение",
    QuestKind.photo: "📝 Ответ на проверку",
    QuestKind.quiz: "🧠 Вопрос дня",
}


REPEAT_TITLES = {
    QuestRepeat.none: "Не повторять",
    QuestRepeat.daily: "Раз в день",
    QuestRepeat.weekly: "Раз в неделю",
    QuestRepeat.monthly: "Раз в месяц",
}


REPEAT_BUTTONS = {
    QuestRepeat.none: "🚫 Не повторять",
    QuestRepeat.daily: "🔁 Раз в день",
    QuestRepeat.weekly: "🔁 Раз в неделю",
    QuestRepeat.monthly: "🔁 Раз в месяц",
}


# Максимум кнопок-квестов в одном списке
LIST_LIMIT = 20


# Заявок на одной странице архива
ARCHIVE_PAGE = 10


# Квестов на одной странице управления
MANAGE_PAGE = 10


STATUS_MARKS = {SubmissionStatus.approved: "✅", SubmissionStatus.rejected: "❌"}


_ARCHIVE_BUTTON = callback_button("🗂 Архив проверенных", "arch:page:0")


def coins(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        word = "монета"
    elif 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        word = "монеты"
    else:
        word = "монет"
    return f"{n} {word}"


def _short(text: str, limit: int = 40) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


HELP_ITEM = ("ℹ️ Помощь", "menu:help")


SURVEY_ITEM = ("📋 Опрос", "sv:start")


WEB_ITEM = ("🌐 Веб-версия", "web:open")


def with_count(text: str, count: int) -> str:
    """«🎁 Награды · 3» — раздел, где что-то ждёт действия."""
    return f"{text} · {count}" if count else text


def menu_for(
    role: Role,
    moderator_review: bool = False,
    help_first: bool = False,
    counts: dict[str, int] | None = None,
    survey: bool = False,
    web: bool = False,
) -> list[dict]:
    """help_first — пользователь ещё не открывал справку: она первой кнопкой, иначе — последней.
    counts — сколько ждёт действия в разделе (payload раздела -> число), показывается на кнопке.
    survey — опрос открыт и ещё не пройден: кнопка «📋 Опрос» первой."""
    items = [HELP_ITEM, *MENUS[role]] if help_first else [*MENUS[role], HELP_ITEM]
    if survey:
        items.insert(0, SURVEY_ITEM)
    if web:
        items.insert(len(items) - (0 if help_first else 1), WEB_ITEM)
    counts = counts or {}
    items = [(with_count(text, counts.get(payload, 0)), payload) for text, payload in items]
    if role == Role.moderator and moderator_review:
        items = [
            ("📋 На проверке", "menu:review") if text == "🔒 На проверке" else (text, payload)
            for text, payload in items
        ]
    buttons = [callback_button(text, payload) for text, payload in items]
    # У админа и модератора разделов много — в два столбца
    return _keyboard(_grid(buttons, 2 if role in (Role.admin, Role.moderator) else 1))


def back_to_menu() -> list[dict]:
    return keyboard([[callback_button("« В меню", "menu:main")]])


def cancel() -> list[dict]:
    return keyboard([[callback_button("✖️ Отмена", "cancel")]])


def _pages(prefix: str, page: int, has_more: bool) -> list[list[dict]]:
    nav = []
    if page > 0:
        nav.append(callback_button("« Новее", f"{prefix}{page - 1}"))
    if has_more:
        nav.append(callback_button("Старше »", f"{prefix}{page + 1}"))
    return [nav] if nav else []


def _grid(buttons: list[dict], per_row: int = 4) -> list[list[dict]]:
    return [buttons[i : i + per_row] for i in range(0, len(buttons), per_row)]


def _class_grid(classes: list[SchoolClass], payload: str) -> list[list[dict]]:
    """Классы сеткой по 4 в ряд: 20 классов — 5 рядов. payload — шаблон с {id}."""
    return _grid([callback_button(_short(c.name, 12), payload.format(id=c.id)) for c in classes])


def web_link(url: str) -> list[dict]:
    return keyboard([[link_button("🌐 Открыть веб-версию", url)]])
