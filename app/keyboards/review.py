"""Проверка ответов: список, награда, отказ с причиной, архив, публикация в канале."""
from app.db.models import Submission, SubmissionStatus
from app.keyboards.base import (
    _ARCHIVE_BUTTON,
    LIST_LIMIT,
    STATUS_MARKS,
    _grid,
    _short,
    coins,
    keyboard,
)
from app.max_api import callback_button


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
        _grid(row, 3)
        + [[callback_button("❌ Отклонить", f"rev:no:{submission_id}")], [_TO_REVIEW_LIST]]
    )


def review_actions(submission_id: int) -> list[dict]:
    return keyboard(
        [
            [
                callback_button("✅ Подтвердить", f"rev:ok:{submission_id}"),
                callback_button("❌ Отклонить", f"rev:no:{submission_id}"),
            ],
            [_TO_REVIEW_LIST],
        ]
    )


# Выйти из заявки, ничего не решив: заявка остаётся на проверке
_TO_REVIEW_LIST = callback_button("« К списку проверок", "menu:review")


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
