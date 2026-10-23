"""Навигация: из глубины разделов — в главное меню одним нажатием."""
from app import keyboards as kb
from app.max_api import callback_button as button


def rows_of(markup) -> list[list[tuple[str, str]]]:
    return [[(b["text"], b["payload"]) for b in row] for row in markup[0]["payload"]["buttons"]]


def test_home_next_to_single_back():
    markup = kb.keyboard([[button("Открыть", "x")], [button("« К списку", "list")]])
    assert rows_of(markup)[-1] == [("« К списку", "list"), (kb.HOME, "menu:main")]


def test_home_in_own_row_after_pagination():
    markup = kb.keyboard([[button("« Новее", "p:0"), button("Старее »", "p:2")]])
    assert rows_of(markup)[-1] == [(kb.HOME, "menu:main")]


def test_no_home_when_not_needed():
    for rows in (
        [[button("« В меню", "menu:main")]],  # уже есть путь в меню
        [[button("« Назад", "reg:back")]],  # регистрация: меню ещё нет
        [[button("✅ Выполнено", "q:done:1")]],  # нет «назад» — не экран раздела
    ):
        assert all(p != "menu:main" or t == "« В меню" for row in rows_of(kb.keyboard(rows)) for t, p in row)
        assert len(rows_of(kb.keyboard(rows))) == len(rows)


def test_deep_screens_get_home():
    """Карточки в глубине разделов: квест ученика, достижение, вопрос."""
    for markup in (kb.quest_info(1), kb.question_card(1, None), kb.question_card(1, "✍️ Ответить")):
        assert ("🏠 В меню", "menu:main") in [b for row in rows_of(markup) for b in row]
