"""Награды и магазин: каталог, покупка, Колесо Фортуны, выдача заказов."""
from app.db.models import Order, OrderStatus, Reward, RewardKind
from app.keyboards.base import (
    LIST_LIMIT,
    _pages,
    _short,
    back_to_menu,
    keyboard,
    locked,
    with_count,
)
from app.max_api import callback_button

REWARD_PAGE = 10


ORDERS_PAGE = 10


ORDER_STATUS_TITLES = {
    OrderStatus.pending: "⏳ ждёт выдачи",
    OrderStatus.issued: "✅ выдано",
    OrderStatus.cancelled: "↩️ отменено",
}


def reward_admin_list(
    items: list[Reward], page: int, has_more: bool, readonly: bool = False, pending: int = 0
) -> list[dict]:
    """pending — заказов ждут выдачи (выдавать может и модератор)."""
    rows = [[callback_button(with_count("📦 Выдача", pending), "ord:list")]]
    rows += [[locked("Новая награда")]] if readonly else [[callback_button("➕ Новая награда", "rw:new")]]
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


def orders_list(items: list[Order], staff: bool = False) -> list[dict]:
    """staff — админ или модератор: выдача у них внутри «Наград»."""
    rows = []
    for o in items[:LIST_LIMIT]:
        text = f"{_short(o.user.display_name, 24)} · {_short(o.reward.title, 24)}"
        rows.append([callback_button(text, f"ord:open:{o.id}")])
    rows.append([_back_to_rewards() if staff else callback_button("« В меню", "menu:main")])
    return keyboard(rows)


def _back_to_rewards() -> dict:
    return callback_button("« К наградам", "menu:rewards")


def no_orders(staff: bool = False) -> list[dict]:
    return keyboard([[_back_to_rewards()]]) if staff else back_to_menu()


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
