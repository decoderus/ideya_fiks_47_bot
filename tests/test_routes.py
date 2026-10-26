"""Таблицы маршрутов бота: каждый обработчик существует, каждая кнопка куда-то ведёт."""
import re
from pathlib import Path

from app.bot import CALLBACK_ROUTES, COMMANDS, KEEP_STATE, TEXT_STEPS, Bot, route_for

APP = Path(__file__).resolve().parent.parent / "app"
# Эти кнопки обрабатываются до маршрутизации (перезапуск, демо-режим)
BEFORE_ROUTING = ("menu:restart", "demo:")


def test_handlers_exist():
    names = set(CALLBACK_ROUTES.values()) | set(COMMANDS.values()) | {h for _, h, _ in TEXT_STEPS}
    missing = sorted(name for name in names if not callable(getattr(Bot, name, None)))
    assert missing == []


def test_text_steps_do_not_overlap():
    seen: dict[str, str] = {}
    for states, handler, _ in TEXT_STEPS:
        for state in states:
            assert state not in seen, f"шаг {state}: {seen[state]} и {handler}"
            seen[state] = handler


def test_longest_prefix_wins():
    assert route_for("bc:read:5") == "_inbox_callback"
    assert route_for("bc:aud:all") == "_broadcasts"
    assert route_for("menu:rating") == "_rating_callback"
    assert route_for("rate:class:3") == "_rating_callback"
    assert route_for("menu:main") == "_common_callback"
    assert route_for("menu:mainx") is None  # точный ключ не работает как префикс
    assert route_for("nothing") is None


def test_every_button_has_route():
    """Каждый payload кнопки, записанный в коде, ведёт в какой-то раздел."""
    payloads = set()
    for path in APP.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for match in re.finditer(r'callback_button\(\s*[^,]+?,\s*f?"([^"]+)"', source, re.S):
            prefix = match.group(1).split("{")[0]
            if prefix:
                payloads.add(prefix)
    assert len(payloads) > 100
    lost = sorted(p for p in payloads if route_for(p) is None and not p.startswith(BEFORE_ROUTING))
    assert lost == []


def test_keep_state_buttons_are_routed():
    assert all(route_for(prefix) for prefix in KEEP_STATE)
