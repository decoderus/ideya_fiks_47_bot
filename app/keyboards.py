from app.db.models import (
    AchievementKind,
    Order,
    OrderStatus,
    Quest,
    QuestKind,
    QuestRepeat,
    Reward,
    RewardKind,
    Role,
    SchoolClass,
    Submission,
    SubmissionStatus,
)
from app.max_api import callback_button
from app.max_api import keyboard as _keyboard
from app.quiz_bank import LETTERS

HOME = "🏠 В меню"


def keyboard(rows: list[list[dict]]) -> list[dict]:
    """Клавиатура экрана. Если на нём есть «« назад», но нет пути в главное меню,
    добавляет «🏠 В меню»: из глубины разделов — в меню одним нажатием.
    Регистрацию не трогает: у незарегистрированного меню ещё нет."""
    buttons = [b for row in rows for b in row]
    payloads = [b["payload"] for b in buttons]
    has_back = any(b["text"].startswith("«") for b in buttons)
    if has_back and "menu:main" not in payloads and not any(p.startswith("reg:") for p in payloads):
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
        ("➕ Новый квест", "menu:new_quest"),
        ("📂 Мои квесты", "mq:list:a:0"),
        ("📋 На проверке", "menu:review"),
        ("❓ Вопросы", "menu:questions"),
        ("📦 Выдача", "ord:list"),
        ("💰 Монеты", "menu:coins"),
        ("🏆 Рейтинг", "menu:rating"),
        ("📒 Журнал успехов", "menu:journal"),
        ("🏫 Мои классы", "menu:classes"),
        ("🔄 Перезапустить", "menu:restart"),
    ],
    Role.admin: [
        ("➕ Новый квест", "menu:new_quest"),
        ("📂 Все квесты", "mq:list:a:0"),
        ("📋 На проверке", "menu:review"),
        ("❓ Вопросы", "menu:questions"),
        ("📦 Выдача", "ord:list"),
        ("💰 Монеты", "menu:coins"),
        ("🏆 Рейтинг", "menu:rating"),
        ("🎁 Награды", "menu:rewards"),
        ("📣 Оповещения", "menu:broadcast"),
        ("🏫 Классы", "menu:classes"),
        ("👩‍🏫 Учителя", "menu:teachers"),
        ("🆕 Регистрации", "enl:0"),
        ("📊 Статистика", "menu:stats"),
        ("📒 Журнал успехов", "menu:journal"),
        ("🏅 Достижения", "menu:achievements"),
        ("📅 Сезоны", "menu:seasons"),
        ("🔄 Перезапустить", "menu:restart"),
    ],
    # Как у админа; недоступное — с замком. Награды — только просмотр,
    # оповещения — на подтверждение админу
    Role.moderator: [
        ("➕ Новый квест", "menu:new_quest"),
        ("📂 Все квесты", "mq:list:a:0"),
        ("🔒 На проверке", LOCKED),
        ("❓ Вопросы", "menu:questions"),
        ("📦 Выдача", "ord:list"),
        ("🔒 Монеты", LOCKED),
        ("🏆 Рейтинг", "menu:rating"),
        ("🎁 Награды", "menu:rewards"),
        ("📣 Оповещения", "menu:broadcast"),
        ("🏫 Классы", "menu:classes"),
        ("👩‍🏫 Учителя", "menu:teachers"),
        ("🆕 Регистрации", "enl:0"),
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


def menu_for(role: Role, moderator_review: bool = False) -> list[dict]:
    items = MENUS[role]
    if role == Role.moderator and moderator_review:
        items = [
            ("📋 На проверке", "menu:review") if text == "🔒 На проверке" else (text, payload)
            for text, payload in items
        ]
    buttons = [callback_button(text, payload) for text, payload in items]
    # У админа и модератора разделов много — в два столбца
    return keyboard(_grid(buttons, 2 if role in (Role.admin, Role.moderator) else 1))


def back_to_menu() -> list[dict]:
    return keyboard([[callback_button("« В меню", "menu:main")]])


def cancel() -> list[dict]:
    return keyboard([[callback_button("✖️ Отмена", "cancel")]])


def quest_kinds() -> list[dict]:
    # Вопрос дня создаётся отдельно (банк вопросов), в мастере его нет
    rows = [
        [callback_button(title, f"nq:kind:{k.value}")]
        for k, title in KIND_TITLES.items()
        if k != QuestKind.quiz
    ]
    rows.append([callback_button("✖️ Отмена", "cancel")])
    return keyboard(rows)


def quest_photo_choice() -> list[dict]:
    return keyboard(
        [
            [callback_button("➡️ Без картинки", "nq:photo:none")],
            [callback_button("✖️ Отмена", "cancel")],
        ]
    )


def quest_repeats() -> list[dict]:
    rows = [[callback_button(title, f"nq:rep:{r.value}")] for r, title in REPEAT_BUTTONS.items()]
    rows.append([callback_button("✖️ Отмена", "cancel")])
    return keyboard(rows)


def audience(classes: list[SchoolClass], prefix: str, allow_all: bool = True) -> list[dict]:
    """Выбор «вся школа» или класс. prefix: nq:aud (квест) или bc:aud (оповещение)."""
    rows = [[callback_button("🏫 Вся школа", f"{prefix}:all")]] if allow_all else []
    for c in classes:
        rows.append([callback_button(f"👥 {_short(c.name)}", f"{prefix}:{c.id}")])
    rows.append([callback_button("✖️ Отмена", "cancel")])
    return keyboard(rows)


def quest_audience(classes: list[SchoolClass], allow_all: bool = True) -> list[dict]:
    return audience(classes, "nq:aud", allow_all)


def quest_list(items: list[tuple[Quest, bool]]) -> list[dict]:
    rows = []
    for quest, pending in items[:LIST_LIMIT]:
        mark = "⏳ " if pending else ""
        if quest.repeat != QuestRepeat.none:
            mark += "🔁 "
        text = f"{mark}{_short(quest.title)} · {reward_short(quest)}"
        rows.append([callback_button(text, f"q:open:{quest.id}")])
    rows.append([callback_button("« В меню", "menu:main")])
    return keyboard(rows)


def quest_card(quest: Quest) -> list[dict]:
    if quest.kind == QuestKind.auto:
        action = callback_button("✅ Выполнено", f"q:done:{quest.id}")
    else:
        action = callback_button("📝 Отправить ответ", f"q:photo:{quest.id}")
    return keyboard(
        [
            [action],
            [callback_button("❓ Задать вопрос", f"qa:ask:{quest.id}")],
            [callback_button("« К квестам", "menu:quests")],
        ]
    )


def quest_info(quest_id: int) -> list[dict]:
    """Карточка квеста, который сейчас не выполнить (на проверке, уже выполнен)."""
    return keyboard(
        [
            [callback_button("❓ Задать вопрос", f"qa:ask:{quest_id}")],
            [callback_button("« К квестам", "menu:quests")],
        ]
    )


# --- вопросы учеников по квестам ---


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


def new_quest_notice(quest_id: int) -> list[dict]:
    return keyboard([[callback_button("🎯 Открыть квест", f"q:open:{quest_id}")]])


def to_quests() -> list[dict]:
    return keyboard(
        [
            [callback_button("🎯 К квестам", "menu:quests")],
            [callback_button("« В меню", "menu:main")],
        ]
    )


def review_list(items: list[Submission]) -> list[dict]:
    rows = []
    for s in items[:LIST_LIMIT]:
        text = f"{_short(s.user.display_name, 24)} · {_short(s.quest.title, 24)}"
        rows.append([callback_button(text, f"rev:open:{s.id}")])
    rows.append([_ARCHIVE_BUTTON])
    rows.append([callback_button("« В меню", "menu:main")])
    return keyboard(rows)


def review_empty() -> list[dict]:
    return keyboard([[_ARCHIVE_BUTTON], [callback_button("« В меню", "menu:main")]])


def reward_short(quest) -> str:
    """«300» или «100–500» — для кнопок."""
    if quest.reward_min is None:
        return str(quest.reward)
    return f"{quest.reward_min}–{quest.reward}"


def reward_text(quest) -> str:
    """«300 монет» или «100–500 монет» — для текста."""
    if quest.reward_min is None:
        return coins(quest.reward)
    return f"{quest.reward_min}–{quest.reward} монет"


def review_amounts(submission_id: int, amounts: list[int]) -> list[dict]:
    """Квест с диапазоном награды: проверяющий выбирает сумму."""
    row = [callback_button(f"✅ {a}", f"rev:ok:{submission_id}:{a}") for a in amounts]
    return keyboard(
        _grid(row, 3) + [[callback_button("❌ Отклонить", f"rev:no:{submission_id}")]]
    )


def review_actions(submission_id: int) -> list[dict]:
    return keyboard(
        [
            [
                callback_button("✅ Подтвердить", f"rev:ok:{submission_id}"),
                callback_button("❌ Отклонить", f"rev:no:{submission_id}"),
            ]
        ]
    )


def open_review(submission_id: int) -> list[dict]:
    return keyboard([[callback_button("Открыть заявку", f"rev:open:{submission_id}")]])


def to_review() -> list[dict]:
    return keyboard(
        [[callback_button("📋 К списку проверок", "menu:review")], [_ARCHIVE_BUTTON]]
    )


def archive_list(
    items: list[Submission], page: int, has_more: bool, daily: str | None = None, in_daily: bool = False
) -> list[dict]:
    """daily — подпись кнопки раздела ежедневных квестов (только у админа);
    in_daily — открыт сам этот раздел."""
    section = "arch:daily" if in_daily else "arch:page"
    rows = []
    for s in items:
        text = (
            f"{STATUS_MARKS.get(s.status, '')} {_short(s.user.display_name, 22)}"
            f" · {_short(s.quest.title, 22)}"
        )
        rows.append([callback_button(text, f"arch:open:{s.id}")])
    nav = []
    if page > 0:
        nav.append(callback_button("« Новее", f"{section}:{page - 1}"))
    if has_more:
        nav.append(callback_button("Старше »", f"{section}:{page + 1}"))
    if nav:
        rows.append(nav)
    if in_daily:
        rows.append([callback_button("🗂 Весь архив", "arch:page:0")])
    elif daily:
        rows.append([callback_button(daily, "arch:daily:0")])
    rows.append([callback_button("📋 К проверке", "menu:review")])
    return keyboard(rows)


def to_archive() -> list[dict]:
    return keyboard([[callback_button("🗂 К архиву", "arch:page:0")]])


def override(submission: Submission) -> list[dict]:
    """Админ меняет решение по проверенной заявке."""
    if submission.status == SubmissionStatus.approved:
        flip = callback_button("↩️ Отменить: не засчитывать", f"rev:flip:{submission.id}")
    else:
        flip = callback_button("↩️ Изменить: засчитать", f"rev:flip:{submission.id}")
    return keyboard([[flip], [callback_button("🗂 К архиву", "arch:page:0")]])


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


def managed_list(
    items: list[Quest], active: bool, page: int, has_more: bool, viewer_id: int | None = None
) -> list[dict]:
    """viewer_id: учитель или модератор; чужие квесты: 🏫 — администрации, 👥 — коллег."""
    flag = "a" if active else "c"

    def label(q: Quest) -> str:
        mark = ""
        if viewer_id is not None and q.author_id != viewer_id:
            mark = "🏫 " if q.author.role in (Role.admin, Role.moderator) else "👥 "
        mark = APPROVAL_MARKS.get(q.approval, "") + mark
        return f"{mark}{_short(q.title)} · {reward_short(q)}"

    rows = [[callback_button(label(q), f"mq:open:{q.id}")] for q in items]
    nav = []
    if page > 0:
        nav.append(callback_button("« Новее", f"mq:list:{flag}:{page - 1}"))
    if has_more:
        nav.append(callback_button("Старше »", f"mq:list:{flag}:{page + 1}"))
    if nav:
        rows.append(nav)
    switch = ("🔒 Закрытые", "mq:list:c:0") if active else ("🟢 Активные", "mq:list:a:0")
    rows.append([callback_button(*switch)])
    rows.append([callback_button("« В меню", "menu:main")])
    return keyboard(rows)


APPROVAL_MARKS = {"pending": "⏳ ", "rework": "✏️ "}


def managed_card(
    quest: Quest, can_manage: bool = True, show_locked: bool = False, is_admin: bool = False
) -> list[dict]:
    if quest.approval is not None and can_manage:
        edit = callback_button("✏️ Изменить", f"mq:edit:{quest.id}")
        rows = [[edit]]
        if quest.approval == "pending" and is_admin:
            rows.insert(0, _approval_row(quest.id))
        elif quest.approval == "rework":
            rows.append([callback_button("📨 Отправить на подтверждение", f"mq:resub:{quest.id}")])
        rows.append([callback_button("« К списку", "mq:list:c:0")])
        return keyboard(rows)
    if not can_manage and show_locked:
        back = "mq:list:a:0" if quest.is_active else "mq:list:c:0"
        return keyboard(
            [[locked("✏️ Изменить")], [locked("Закрыть квест")], [callback_button("« К списку", back)]]
        )
    if not can_manage:
        back = "mq:list:a:0" if quest.is_active else "mq:list:c:0"
        return keyboard(
            [
                [callback_button("📋 На проверке", "menu:review")],
                [callback_button("« К списку", back)],
            ]
        )
    edit = callback_button("✏️ Изменить", f"mq:edit:{quest.id}")
    if quest.is_active:
        action = callback_button("⛔ Закрыть квест", f"mq:close:{quest.id}")
        back = "mq:list:a:0"
    else:
        action = callback_button("🟢 Открыть снова", f"mq:reopen:{quest.id}")
        back = "mq:list:c:0"
    return keyboard([[edit], [action], [callback_button("« К списку", back)]])


def confirm_close(quest_id: int) -> list[dict]:
    return keyboard(
        [
            [
                callback_button("🔒 Да, закрыть", f"mq:close_ok:{quest_id}"),
                callback_button("Нет", f"mq:open:{quest_id}"),
            ]
        ]
    )


# --- оповещения ---

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


# --- награды и магазин ---

REWARD_PAGE = 10
ORDERS_PAGE = 10

ORDER_STATUS_TITLES = {
    OrderStatus.pending: "⏳ ждёт выдачи",
    OrderStatus.issued: "✅ выдано",
    OrderStatus.cancelled: "↩️ отменено",
}


def _pages(prefix: str, page: int, has_more: bool) -> list[list[dict]]:
    nav = []
    if page > 0:
        nav.append(callback_button("« Новее", f"{prefix}{page - 1}"))
    if has_more:
        nav.append(callback_button("Старше »", f"{prefix}{page + 1}"))
    return [nav] if nav else []


def reward_admin_list(
    items: list[Reward], page: int, has_more: bool, readonly: bool = False
) -> list[dict]:
    rows = [[locked("Новая награда")]] if readonly else [[callback_button("➕ Новая награда", "rw:new")]]
    for r in items:
        mark = "" if r.is_active else "🙈 "
        rows.append([callback_button(f"{mark}{_short(r.title)} · {r.price}", f"rw:open:{r.id}")])
    rows += _pages("rw:list:", page, has_more)
    rows.append([callback_button("« В меню", "menu:main")])
    return keyboard(rows)


def reward_stock_choice() -> list[dict]:
    return keyboard(
        [
            [callback_button("♾ Без ограничения", "rw:stock:inf")],
            [callback_button("✖️ Отмена", "cancel")],
        ]
    )


def reward_photo_choice() -> list[dict]:
    return keyboard(
        [
            [callback_button("Без фото", "rw:photo:none")],
            [callback_button("✖️ Отмена", "cancel")],
        ]
    )


def reward_admin_card(reward: Reward, readonly: bool = False) -> list[dict]:
    if readonly:
        return keyboard(
            [
                [locked("Цена"), locked("Количество")],
                [locked("Скрыть из магазина" if reward.is_active else "Вернуть в магазин")],
                [callback_button("« К наградам", "rw:list:0")],
            ]
        )
    visibility = (
        callback_button("🙈 Скрыть из магазина", f"rw:hide:{reward.id}")
        if reward.is_active
        else callback_button("👁 Вернуть в магазин", f"rw:show:{reward.id}")
    )
    return keyboard(
        [
            [
                callback_button("💰 Цена", f"rw:price:{reward.id}"),
                callback_button("📦 Количество", f"rw:qty:{reward.id}"),
            ],
            [visibility],
            [callback_button("« К наградам", "rw:list:0")],
        ]
    )


def to_rewards() -> list[dict]:
    return keyboard([[callback_button("« К наградам", "rw:list:0")]])


def shop_list(items: list[Reward], balance: int) -> list[dict]:
    rows = []
    for r in items[:LIST_LIMIT]:
        mark = "🔒 " if r.price > balance else ""
        if r.kind == RewardKind.lottery:
            mark += "🎡 "
        rows.append([callback_button(f"{mark}{_short(r.title)} · {r.price}", f"shop:open:{r.id}")])
    rows.append([callback_button("📦 Мои покупки", "shop:mine:0")])
    rows.append([callback_button("« В меню", "menu:main")])
    return keyboard(rows)


def shop_card(reward: Reward, affordable: bool) -> list[dict]:
    rows = []
    if affordable:
        action = "🎡 Крутить за" if reward.kind == RewardKind.lottery else "🛒 Купить за"
        rows.append([callback_button(f"{action} {reward.price}", f"shop:buy:{reward.id}")])
    rows.append([callback_button("« В магазин", "menu:shop")])
    return keyboard(rows)


def shop_confirm(reward_id: int) -> list[dict]:
    return keyboard(
        [
            [
                callback_button("✅ Да, купить", f"shop:buy_ok:{reward_id}"),
                callback_button("Нет", f"shop:open:{reward_id}"),
            ]
        ]
    )


def after_spin(reward_id: int, can_repeat: bool) -> list[dict]:
    rows = []
    if can_repeat:
        rows.append([callback_button("🎡 Крутить ещё", f"shop:buy:{reward_id}")])
    rows.append([callback_button("🛒 В магазин", "menu:shop")])
    rows.append([callback_button("« В меню", "menu:main")])
    return keyboard(rows)


def after_purchase() -> list[dict]:
    return keyboard(
        [
            [callback_button("📦 Мои покупки", "shop:mine:0")],
            [callback_button("🛒 В магазин", "menu:shop")],
            [callback_button("« В меню", "menu:main")],
        ]
    )


def my_orders_nav(page: int, has_more: bool) -> list[dict]:
    rows = _pages("shop:mine:", page, has_more)
    rows.append([callback_button("🛒 В магазин", "menu:shop")])
    rows.append([callback_button("« В меню", "menu:main")])
    return keyboard(rows)


def orders_list(items: list[Order]) -> list[dict]:
    rows = []
    for o in items[:LIST_LIMIT]:
        text = f"{_short(o.user.display_name, 24)} · {_short(o.reward.title, 24)}"
        rows.append([callback_button(text, f"ord:open:{o.id}")])
    rows.append([callback_button("« В меню", "menu:main")])
    return keyboard(rows)


def order_actions(order_id: int, can_cancel: bool = True) -> list[dict]:
    actions = [callback_button("✅ Выдано", f"ord:issue:{order_id}")]
    if can_cancel:
        actions.append(callback_button("↩️ Отменить", f"ord:cancel:{order_id}"))
    else:
        actions.append(locked("Отменить"))
    return keyboard([actions, [callback_button("📦 К выдаче", "ord:list")]])


def order_cancel_confirm(order_id: int) -> list[dict]:
    return keyboard(
        [
            [
                callback_button("↩️ Да, отменить", f"ord:cancel_ok:{order_id}"),
                callback_button("Нет", f"ord:open:{order_id}"),
            ]
        ]
    )


def order_notice(order_id: int) -> list[dict]:
    return keyboard([[callback_button("Открыть заказ", f"ord:open:{order_id}")]])


def to_orders() -> list[dict]:
    return keyboard([[callback_button("📦 К выдаче", "ord:list")]])


# --- журнал успехов ---

JOURNAL_PAGE = 10


def journal_classes(classes: list[SchoolClass]) -> list[dict]:
    rows = [[callback_button(f"👥 {_short(c.name)}", f"jr:cls:{c.id}:0")] for c in classes]
    rows.append([callback_button("« В меню", "menu:main")])
    return keyboard(rows)


def journal_students(
    students: list,
    done: dict[int, int],
    class_id: int,
    page: int,
    has_more: bool,
    to_classes: bool,
) -> list[dict]:
    rows = [
        [
            callback_button(
                f"{_short(s.display_name, 26)} · 🎯{done.get(s.id, 0)} · 💰{s.balance}",
                f"jr:st:{s.id}",
            )
        ]
        for s in students
    ]
    nav = []
    if page > 0:
        nav.append(callback_button("« Назад", f"jr:cls:{class_id}:{page - 1}"))
    if has_more:
        nav.append(callback_button("Дальше »", f"jr:cls:{class_id}:{page + 1}"))
    if nav:
        rows.append(nav)
    if to_classes:
        rows.append([callback_button("« К классам", "menu:journal")])
    rows.append([callback_button("« В меню", "menu:main")])
    return keyboard(rows)


def journal_card(student) -> list[dict]:
    return keyboard(
        [
            [callback_button("« К списку", f"jr:cls:{student.class_id}:0")],
            [callback_button("« В меню", "menu:main")],
        ]
    )


# --- монеты ---

COINS_PAGE = 10


def coins_classes(classes: list[SchoolClass]) -> list[dict]:
    rows = [[callback_button(f"👥 {_short(c.name)}", f"coins:cls:{c.id}:0")] for c in classes]
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


# --- регистрация ---


def _grid(buttons: list[dict], per_row: int = 4) -> list[list[dict]]:
    return [buttons[i : i + per_row] for i in range(0, len(buttons), per_row)]


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


# --- проверка зачисления ученика ---


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


# --- классы (админ) ---

CLASS_STUDENTS_PAGE = 10


def classes_overview(
    classes: list[SchoolClass], admin: bool = True, show_locked: bool = False
) -> list[dict]:
    if admin:
        rows = [[callback_button("➕ Добавить классы", "cls:add")]]
    else:
        rows = [[locked("Добавить классы")]] if show_locked else []
    rows += _grid([callback_button(c.name, f"cls:open:{c.id}") for c in classes])
    rows.append([callback_button("« В меню", "menu:main")])
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


# --- учителя (админ) ---


def teachers_overview(requests: list, teachers: list) -> list[dict]:
    rows = [[callback_button(f"⏳ {_short(r.full_name, 30)}", f"tr:open:{r.id}")] for r in requests]
    for t in teachers:
        names = ", ".join(c.name for c in t.teacher_classes) or "без классов"
        rows.append([callback_button(f"{_short(t.display_name, 24)} · {_short(names, 20)}", f"tc:open:{t.id}")])
    rows.append([callback_button("« В меню", "menu:main")])
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


# --- журнал регистраций и статистика (админ) ---

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
    rows.append([callback_button("« В меню", "menu:main")])
    return keyboard(rows)


def to_enrollments() -> list[dict]:
    return keyboard([[callback_button("🆕 К регистрациям", "enl:0")]])


def stats(admin: bool = True) -> list[dict]:
    if admin:
        logs = [
            callback_button("💰 Движение монет", "log:coins:0"),
            callback_button("🛡 Действия персонала", "log:staff:0"),
        ]
    else:
        logs = [locked("Движение монет"), locked("Действия персонала")]
    return keyboard(
        [
            logs,
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


# --- достижения ---

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


# --- редактирование квеста ---


def quest_edit_menu(quest_id: int) -> list[dict]:
    def b(text: str, field: str) -> dict:
        return callback_button(text, f"mq:ef:{field}:{quest_id}")

    return keyboard(
        [
            [b("✏️ Название", "title"), b("📝 Описание", "description")],
            [b("💰 Награда", "reward"), b("🔁 Периодичность", "repeat")],
            [b("🖼 Картинка", "photo"), b("👩‍🏫 Проверяющие", "reviewers")],
            [callback_button("« К квесту", f"mq:open:{quest_id}")],
        ]
    )


def quest_edit_repeats(quest_id: int) -> list[dict]:
    rows = [
        [callback_button(title, f"mq:rep:{r.value}:{quest_id}")]
        for r, title in REPEAT_BUTTONS.items()
    ]
    rows.append([callback_button("« Назад", f"mq:edit:{quest_id}")])
    return keyboard(rows)


def quest_edit_photo(quest_id: int, has_photo: bool) -> list[dict]:
    rows = []
    if has_photo:
        rows.append([callback_button("🗑 Убрать картинку", f"mq:photo_rm:{quest_id}")])
    rows.append([callback_button("✖️ Отмена", f"mq:edit:{quest_id}")])
    return keyboard(rows)


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
            [callback_button("🎯 Квесты", "menu:quests")],
            [toggle],
            [callback_button("« В меню", "menu:main")],
        ]
    )


def reminder_actions(quests: list[Quest]) -> list[dict]:
    rows = [[callback_button(f"🎯 {_short(q.title)}", f"q:open:{q.id}")] for q in quests[:5]]
    rows.append([callback_button("👤 Профиль", "menu:profile")])
    rows.append([callback_button("🔕 Не напоминать", "rem:off")])
    return keyboard(rows)


# --- сезоны ---


def seasons_overview(seasons: list, readonly: bool = False) -> list[dict]:
    if readonly:
        return keyboard(
            [
                [locked("Изменить сезоны")],
                [callback_button("📜 Прошлые сезоны", "sz:archive")],
                [callback_button("« В меню", "menu:main")],
            ]
        )
    rows = [[callback_button(f"📅 {_short(s.title, 34)}", f"sz:open:{s.id}")] for s in seasons]
    rows.append([callback_button("➕ Добавить сезон", "sz:add")])
    rows.append([callback_button("« В меню", "menu:main")])
    return keyboard(rows)


def season_card(season_id: int, editable: bool) -> list[dict]:
    rows = [[callback_button("✏️ Название", f"sz:ren:{season_id}")]]
    if editable:
        rows.append(
            [
                callback_button("📅 Дата начала", f"sz:move:{season_id}"),
                callback_button("🗑 Удалить", f"sz:del:{season_id}"),
            ]
        )
    rows.append([callback_button("« К сезонам", "menu:seasons")])
    return keyboard(rows)


def season_archive(seasons: list) -> list[dict]:
    rows = [[callback_button(f"🏁 {_short(s.title, 34)}", f"sz:res:{s.id}")] for s in reversed(seasons)]
    rows.append([callback_button("🏆 К рейтингу", "rate:school")])
    return keyboard(rows)


def season_results_nav() -> list[dict]:
    return keyboard(
        [
            [callback_button("📜 Другие сезоны", "sz:archive")],
            [callback_button("🏆 К рейтингу", "rate:school")],
        ]
    )


def season_notice(season_id: int) -> list[dict]:
    return keyboard(
        [
            [callback_button("🏁 Итоги сезона", f"sz:res:{season_id}")],
            [callback_button("🏆 Новый рейтинг", "rate:school")],
        ]
    )



# --- модераторы ---


def teacher_approval_undo(request_id: int) -> list[dict]:
    """Админу: модератор подтвердил учителя — можно отменить."""
    return keyboard([[callback_button("↩️ Отменить подтверждение", f"tr:undo:{request_id}")]])


def draft_actions(draft_id: int) -> list[dict]:
    """Админу: оповещение модератора ждёт подтверждения."""
    return keyboard(
        [
            [
                callback_button("📤 Отправить", f"bcd:ok:{draft_id}"),
                callback_button("❌ Отклонить", f"bcd:no:{draft_id}"),
            ]
        ]
    )


def _approval_row(quest_id: int) -> list[dict]:
    return [
        callback_button("✅ Опубликовать", f"mq:apr:{quest_id}"),
        callback_button("↩️ На доработку", f"mq:rwk:{quest_id}"),
    ]


def quest_approval(quest_id: int) -> list[dict]:
    """Админу: опубликовать квест модератора или вернуть на доработку."""
    return keyboard(
        [_approval_row(quest_id), [callback_button("📂 Открыть квест", f"mq:open:{quest_id}")]]
    )


def quest_rework(quest_id: int) -> list[dict]:
    """Админ пишет, что доработать, или возвращает без комментария."""
    return keyboard(
        [
            [callback_button("Вернуть без комментария", f"mq:rwk0:{quest_id}")],
            [callback_button("« Назад", f"mq:open:{quest_id}")],
        ]
    )


def new_quest_notice_admin(quest_id: int) -> list[dict]:
    """Админу: квест модератора — открыть, чтобы при необходимости закрыть или поправить."""
    return keyboard([[callback_button("📂 Открыть квест", f"mq:open:{quest_id}")]])


def quiz_options(quest_id: int, question) -> list[dict]:
    """Четыре варианта ответа на вопрос дня."""
    rows = [
        [callback_button(f"{LETTERS[i]}. {_short(option, 40)}", f"q:quiz:{quest_id}:{question.index}:{i}")]
        for i, option in enumerate(question.options)
    ]
    rows.append([callback_button("❓ Задать вопрос", f"qa:ask:{quest_id}")])
    rows.append([callback_button("« К квестам", "menu:quests")])
    return keyboard(rows)


def quest_reviewers(quest_id: int, teachers: list, chosen: set[int]) -> list[dict]:
    rows = [
        [callback_button(f"{'✅ ' if t.id in chosen else ''}{_short(t.display_name, 36)}", f"mq:rv:{t.id}:{quest_id}")]
        for t in teachers
    ]
    rows.append([callback_button("✔️ Готово", f"mq:open:{quest_id}")])
    return keyboard(rows)


def reject_reasons(submission_id: int, reasons: list[str]) -> list[dict]:
    """Проверяющий отклоняет ответ: готовая причина, без причины или назад."""
    rows = [
        [callback_button(f"❌ {reason}", f"rev:nr:{submission_id}:{i + 1}")]
        for i, reason in enumerate(reasons)
    ]
    rows.append([callback_button("✍️ Написать свою причину", f"rev:nw:{submission_id}")])
    rows.append([callback_button("Отклонить без причины", f"rev:nr:{submission_id}:0")])
    rows.append([callback_button("« Назад", f"rev:back:{submission_id}")])
    return keyboard(rows)


def reject_write(submission_id: int) -> list[dict]:
    return keyboard(
        [
            [callback_button("« К выбору причины", f"rev:no:{submission_id}")],
            [callback_button("« Назад к заявке", f"rev:back:{submission_id}")],
        ]
    )


def publication_actions(submission_id: int) -> list[dict]:
    """Проверяющий решает, публиковать ли засчитанный ответ в канале школы."""
    return keyboard(
        [
            [callback_button("📢 Опубликовать", f"pub:ok:{submission_id}")],
            [callback_button("✏️ На доработку автору", f"pub:fix:{submission_id}")],
            [callback_button("🚫 Не публиковать", f"pub:no:{submission_id}")],
            [callback_button("📋 К списку проверок", "menu:review")],
        ]
    )


def publication_note(submission_id: int) -> list[dict]:
    return keyboard([[callback_button("« Назад", f"pub:back:{submission_id}")]])


def publication_fix(submission_id: int) -> list[dict]:
    """Автору: прислать исправленный вариант для публикации."""
    return keyboard(
        [
            [callback_button("✍️ Прислать исправленный вариант", f"pub:send:{submission_id}")],
            [callback_button("« В меню", "menu:main")],
        ]
    )


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
    rows, row = [], []
    for c in classes:
        row.append(callback_button(_short(c.name, 16), f"{prefix}:{c.id}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
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
