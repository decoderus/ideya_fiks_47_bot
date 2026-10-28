"""Сезоны-четверти."""
from app.keyboards.base import _short, keyboard, locked
from app.max_api import callback_button


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
