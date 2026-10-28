"""Квесты: создание, список и карточка, управление, редактирование, согласование, вопрос дня."""
from app.db.models import Quest, QuestKind, QuestRepeat, Role, SchoolClass
from app.keyboards.base import (
    KIND_TITLES,
    LIST_LIMIT,
    REPEAT_BUTTONS,
    _class_grid,
    _short,
    keyboard,
    locked,
)
from app.keyboards.review import reward_short
from app.max_api import callback_button
from app.quiz_bank import LETTERS


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
    rows += _class_grid(classes, prefix + ":{id}")
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


def new_quest_notice(quest_id: int) -> list[dict]:
    return keyboard([[callback_button("🎯 Открыть квест", f"q:open:{quest_id}")]])


def to_quests() -> list[dict]:
    return keyboard(
        [
            [callback_button("🎯 К квестам", "menu:quests")],
            [callback_button("« В меню", "menu:main")],
        ]
    )


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

    rows = [[callback_button("➕ Новый квест", "menu:new_quest")]]
    rows += [[callback_button(label(q), f"mq:open:{q.id}")] for q in items]
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
