"""Магазин: награды (создаёт админ), покупки учеников, выдача и отмена."""
import random
from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Order, OrderStatus, Reward, RewardKind, Role, User
from app.services.classes import teachers_of

TITLE_MAX = 128
DESCRIPTION_MAX = 2000
PRICE_MAX = 1_000_000
STOCK_MAX = 100_000


# Системный генератор: результат колеса фортуны нельзя предсказать.
# В тестах подменяется на предсказуемый.
rng = random.SystemRandom()


class ShopError(Exception):
    """Ошибка, текст которой можно показать пользователю."""


# --- награды (админ) ---


def can_manage(user: User) -> bool:
    return user.role == Role.admin


def parse_price(text: str) -> int:
    try:
        price = int(text.strip())
    except ValueError:
        raise ShopError("Цена должна быть целым числом.")
    if not 1 <= price <= PRICE_MAX:
        raise ShopError(f"Цена должна быть от 1 до {PRICE_MAX}.")
    return price


def parse_stock(text: str) -> int:
    try:
        stock = int(text.strip())
    except ValueError:
        raise ShopError("Количество должно быть целым числом.")
    if not 0 <= stock <= STOCK_MAX:
        raise ShopError(f"Количество должно быть от 0 до {STOCK_MAX}.")
    return stock


async def create_reward(
    session: AsyncSession,
    author: User,
    title: str,
    description: str,
    price: int,
    stock: int | None,
    photo: dict | None,
) -> Reward:
    if not can_manage(author):
        raise ShopError("Награды может создавать только админ.")
    reward = Reward(
        title=title[:TITLE_MAX],
        description=description[:DESCRIPTION_MAX],
        price=price,
        stock=stock,
        photo=photo,
        author_id=author.id,
    )
    session.add(reward)
    await session.commit()
    return reward


async def get_managed(session: AsyncSession, user: User, reward_id: int) -> Reward:
    reward = await session.get(Reward, reward_id)
    if reward is None or not can_manage(user):
        raise ShopError("Награда не найдена.")
    return reward


async def all_rewards(
    session: AsyncSession, offset: int, limit: int
) -> tuple[list[Reward], bool]:
    """Для админа: сначала показанные в магазине, затем скрытые; внутри — от дешёвых к дорогим."""
    rows = list(
        await session.scalars(
            select(Reward)
            .order_by(Reward.is_active.desc(), Reward.price, Reward.id)
            .offset(offset)
            .limit(limit + 1)
        )
    )
    return rows[:limit], len(rows) > limit


async def update_reward(
    session: AsyncSession,
    user: User,
    reward_id: int,
    *,
    price: int | None = None,
    stock: int | None = None,
    unlimited: bool = False,
    active: bool | None = None,
) -> Reward:
    reward = await get_managed(session, user, reward_id)
    if price is not None:
        reward.price = price
    if unlimited:
        reward.stock = None
    elif stock is not None:
        reward.stock = stock
    if active is not None:
        reward.is_active = active
    await session.commit()
    return reward


async def lottery_wins(session: AsyncSession, reward_id: int) -> tuple[int, int]:
    """Колесо фортуны: (сколько раз выиграли, сколько монет выплачено)."""
    count, total = (
        await session.execute(
            select(func.count(), func.coalesce(func.sum(Order.prize_won), 0)).where(
                Order.reward_id == reward_id, Order.prize_won > 0
            )
        )
    ).one()
    return count, total


async def lottery_losses(session: AsyncSession, reward_id: int) -> tuple[int, int]:
    """Колесо фортуны: (сколько раз критический провал, сколько монет списано)."""
    count, total = (
        await session.execute(
            select(func.count(), func.coalesce(func.sum(-Order.prize_won), 0)).where(
                Order.reward_id == reward_id, Order.prize_won < 0
            )
        )
    ).one()
    return count, total


async def reward_stats(session: AsyncSession, reward_id: int) -> dict[OrderStatus, int]:
    rows = await session.execute(
        select(Order.status, func.count())
        .where(Order.reward_id == reward_id)
        .group_by(Order.status)
    )
    return {status: count for status, count in rows}


# --- покупки (ученик) ---


def _in_stock():
    return Reward.is_active & or_(Reward.stock.is_(None), Reward.stock > 0)


async def shop_items(session: AsyncSession) -> list[Reward]:
    """Награды, которые сейчас можно купить: дешёвые первыми."""
    return list(
        await session.scalars(select(Reward).where(_in_stock()).order_by(Reward.price, Reward.id))
    )


async def get_for_sale(session: AsyncSession, reward_id: int) -> Reward:
    reward = await session.scalar(select(Reward).where(Reward.id == reward_id, _in_stock()))
    if reward is None:
        raise ShopError("Эта награда закончилась или больше не продаётся.")
    return reward


WIN, NOTHING, LOSS = 1, 0, -1


def roll(win_chance: int, loss_chance: int = 0) -> int:
    """Одно вращение: WIN с шансом win_chance %, LOSS — loss_chance %, иначе NOTHING."""
    value = rng.random() * 100
    if value < win_chance:
        return WIN
    if value < win_chance + loss_chance:
        return LOSS
    return NOTHING


def spin(chance_percent: int) -> bool:
    """Выпал ли выигрыш при шансе chance_percent из 100."""
    return roll(chance_percent) == WIN


async def buy(session: AsyncSession, student: User, reward_id: int) -> Order:
    """Покупка. Для колеса фортуны сразу крутит колесо и начисляет выигрыш."""
    if student.role != Role.student:
        raise ShopError("Покупать награды могут только ученики.")
    reward = await get_for_sale(session, reward_id)
    if student.balance < reward.price:
        raise ShopError(f"Не хватает монет: нужно {reward.price}, у вас {student.balance}.")
    student.balance -= reward.price
    if reward.stock is not None:
        reward.stock -= 1
    order = Order(reward=reward, user=student, price=reward.price)
    if reward.kind == RewardKind.lottery:
        # Выдавать нечего: результат известен сразу
        outcome = roll(reward.win_chance or 0, reward.loss_chance or 0)
        if outcome == WIN:
            order.prize_won = reward.prize or 0
        elif outcome == LOSS:
            # Критический провал: забирает потерю, но баланс не уходит в минус
            order.prize_won = -min(reward.loss or 0, student.balance)
        else:
            order.prize_won = 0
        student.balance += order.prize_won
        order.status = OrderStatus.issued
        order.handled_at = datetime.now(timezone.utc)
    session.add(order)
    await session.commit()
    return order


async def my_orders(
    session: AsyncSession, student: User, offset: int, limit: int
) -> tuple[list[Order], bool]:
    rows = list(
        (
            await session.scalars(
                select(Order)
                .where(Order.user_id == student.id)
                .order_by(Order.id.desc())
                .offset(offset)
                .limit(limit + 1)
            )
        ).unique()
    )
    return rows[:limit], len(rows) > limit


# --- выдача (админ и учитель класса ученика) ---


def can_handle(handler: User, order: Order) -> bool:
    if handler.role in (Role.admin, Role.moderator):
        return True
    return handler.role == Role.teacher and order.user.class_id in handler.teacher_class_ids


async def pending_for(session: AsyncSession, handler: User) -> list[Order]:
    rows = await session.scalars(
        select(Order).where(Order.status == OrderStatus.pending).order_by(Order.id)
    )
    return [o for o in rows.unique() if can_handle(handler, o)]


async def get_order(session: AsyncSession, handler: User, order_id: int) -> Order:
    order = await session.get(Order, order_id)
    if order is None or not can_handle(handler, order):
        raise ShopError("Заказ не найден.")
    return order


async def _close(
    session: AsyncSession, handler: User, order_id: int, status: OrderStatus
) -> Order:
    order = await get_order(session, handler, order_id)
    if order.status != OrderStatus.pending:
        raise ShopError("Этот заказ уже обработан.")
    order.status = status
    order.handler_id = handler.id
    order.handled_at = datetime.now(timezone.utc)
    if status == OrderStatus.cancelled:
        # Возвращаем монеты и награду на склад
        order.user.balance += order.price
        if order.reward.stock is not None:
            order.reward.stock += 1
    await session.commit()
    return order


async def issue(session: AsyncSession, handler: User, order_id: int) -> Order:
    return await _close(session, handler, order_id, OrderStatus.issued)


def can_cancel(handler: User) -> bool:
    """Отмена возвращает монеты — это управление балансом, модератору нельзя."""
    return handler.role in (Role.admin, Role.teacher)


async def cancel(session: AsyncSession, handler: User, order_id: int) -> Order:
    if not can_cancel(handler):
        raise ShopError("Отменять заказы могут учитель класса и админ.")
    return await _close(session, handler, order_id, OrderStatus.cancelled)


async def admins(session: AsyncSession) -> list[User]:
    return list((await session.scalars(select(User).where(User.role == Role.admin))).unique())


async def handlers_for(session: AsyncSession, order: Order) -> list[User]:
    """Кого уведомить о покупке: админов и учителей класса ученика."""
    conditions = [User.role == Role.admin]
    if order.user.class_id is not None:
        conditions.append(teachers_of(order.user.class_id))
    return list((await session.scalars(select(User).where(or_(*conditions)))).unique())
