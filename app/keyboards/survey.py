"""Опрос учеников и учителей."""
from app.keyboards.base import _grid, keyboard, with_count
from app.max_api import callback_button


def survey_options(index: int, options: tuple, nps: bool = False) -> list[dict]:
    buttons = [callback_button(label, f"sv:a:{index}:{key}") for key, label in options]
    rows = _grid(buttons, 4) if nps else [[b] for b in buttons]
    rows.append([callback_button("✖️ Пройду позже", "cancel")])
    return keyboard(rows)


def survey_skip() -> list[dict]:
    return keyboard([[callback_button("Пропустить и завершить", "sv:skip")]])


def survey_invite() -> list[dict]:
    return keyboard([[callback_button("📋 Пройти опрос", "sv:start")]])


def survey_admin(opened: bool, waiting: int) -> list[dict]:
    rows = [[callback_button(with_count("📨 Отправить опрос", waiting), "sv:send")]]
    if opened:
        rows.append([callback_button("🔒 Закрыть опрос", "sv:close")])
    rows.append([callback_button("🎒 Ученики", "sv:res:student"), callback_button("👩‍🏫 Учителя", "sv:res:teacher")])
    rows.append([callback_button("💬 Пожелания", "sv:com")])
    rows.append([callback_button("📊 К статистике", "menu:stats")])
    return keyboard(rows)


def survey_back() -> list[dict]:
    return keyboard([[callback_button("« К опросу", "sv:admin")]])
