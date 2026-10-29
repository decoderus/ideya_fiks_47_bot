"""Награды: управление магазином (админ), магазин и Колесо Фортуны (ученик), выдача покупок."""
import logging

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app import keyboards as kb
from app.db.models import OrderStatus, Reward, RewardKind, Role, User
from app.keyboards import coins
from app.services import audit
from app.services import shop as shop_service
from app.services import users as users_service
from app.services.shop import ShopError
from app.ui import (
    Screen,
    find_image,
    format_date,
    format_day,
    image_attachment,
    parse_id,
)

log = logging.getLogger(__name__)

# Награды: создание и правка (только админ)
ST_RW_TITLE = "rw_title"
ST_RW_DESC = "rw_desc"
ST_RW_PRICE = "rw_price"
ST_RW_STOCK = "rw_stock"
ST_RW_PHOTO = "rw_photo"
ST_RW_EDIT_PRICE = "rw_edit_price"
ST_RW_EDIT_STOCK = "rw_edit_stock"
REWARD_STATES = (
    ST_RW_TITLE, ST_RW_DESC, ST_RW_PRICE, ST_RW_STOCK, ST_RW_PHOTO, ST_RW_EDIT_PRICE, ST_RW_EDIT_STOCK
)

KEEP_STATE = ("rw:stock:", "rw:photo:")


class ShopMixin:
    """Часть класса Bot: награды и магазин."""

    async def _rewards(self, session: AsyncSession, user: User, payload: str) -> Screen | str:
        if user.role == Role.moderator:
            return await self._rewards_view(session, payload)
        if not shop_service.can_manage(user):
            return "Раздел доступен только админам."
        if payload == "menu:rewards" or payload.startswith("rw:list:"):
            return await self._reward_list(session, max(parse_id(payload) or 0, 0))
        if payload == "rw:new":
            await users_service.set_state(session, user, ST_RW_TITLE, {})
            return "🎁 Новая награда. Шаг 1 из 5.\nНапишите название награды.", kb.cancel()
        if payload == "rw:stock:inf":
            if user.state == ST_RW_STOCK:
                data = dict(user.state_data or {}, stock=None)
                await users_service.set_state(session, user, ST_RW_PHOTO, data)
                return self._reward_photo_prompt()
            if user.state == ST_RW_EDIT_STOCK:
                reward_id = (user.state_data or {}).get("reward_id", 0)
                await users_service.set_state(session, user, None)
                return await self._reward_update(
                    session, user, reward_id, "📦 Теперь без ограничения.", unlimited=True
                )
            return "Действие уже завершено или отменено."
        if payload == "rw:photo:none":
            if user.state != ST_RW_PHOTO:
                return "Действие уже завершено или отменено."
            return await self._reward_create(session, user, None)

        reward_id = parse_id(payload) or 0
        try:
            reward = await shop_service.get_managed(session, user, reward_id)
        except ShopError as exc:
            return str(exc)
        if payload.startswith("rw:open:"):
            return await self._reward_card(session, reward)
        if payload.startswith("rw:price:"):
            await users_service.set_state(session, user, ST_RW_EDIT_PRICE, {"reward_id": reward.id})
            return (
                f"Новая цена для «{reward.title}» (сейчас {coins(reward.price)}).\n"
                f"Число от 1 до {shop_service.PRICE_MAX}.",
                kb.cancel(),
            )
        if payload.startswith("rw:qty:"):
            await users_service.set_state(session, user, ST_RW_EDIT_STOCK, {"reward_id": reward.id})
            now = "без ограничения" if reward.stock is None else f"{reward.stock} шт."
            return (
                f"Сколько штук «{reward.title}» доступно? Сейчас: {now}.\n"
                "Напишите число или нажмите «Без ограничения».",
                kb.reward_stock_choice(),
            )
        if payload.startswith(("rw:hide:", "rw:show:")):
            show = payload.startswith("rw:show:")
            note = "👁 Награда снова в магазине." if show else "🙈 Награда скрыта из магазина."
            return await self._reward_update(session, user, reward.id, note, active=show)
        return "Неизвестное действие."

    async def _rewards_view(self, session: AsyncSession, payload: str) -> Screen | str:
        """Модератор: награды только для просмотра, кнопки управления — с замком."""
        if payload == "menu:rewards" or payload.startswith("rw:list:"):
            return await self._reward_list(
                session, max(parse_id(payload) or 0, 0), readonly=True
            )
        if payload.startswith("rw:open:"):
            reward = await session.get(Reward, parse_id(payload) or 0)
            if reward is None:
                return "Награда не найдена."
            return await self._reward_card(session, reward, readonly=True)
        return "🔒 Недоступно модератору: это делает админ."

    async def _reward_list(self, session: AsyncSession, page: int, readonly: bool = False) -> Screen:
        items, has_more = await shop_service.all_rewards(
            session, page * kb.REWARD_PAGE, kb.REWARD_PAGE
        )
        text = "🎁 Награды магазина"
        if items and readonly:
            text += "\nТолько просмотр. 🙈 — скрыта из магазина."
        elif items:
            text += "\nВыберите награду или добавьте новую. 🙈 — скрыта из магазина."
        else:
            text += "\nНаград пока нет. Добавьте первую."
        if page:
            text += f"\nСтраница {page + 1}"
        return text, kb.reward_admin_list(
            items, page, has_more, readonly, pending=self.menu_counts.get("menu:rewards", 0)
        )

    async def _reward_card(
        self, session: AsyncSession, reward, note: str = "", readonly: bool = False
    ) -> Screen:
        stats = await shop_service.reward_stats(session, reward.id)
        pending = stats.get(OrderStatus.pending, 0)
        sold = pending + stats.get(OrderStatus.issued, 0)
        lines = [note, ""] if note else []
        lines.append("🟢 В магазине" if reward.is_active else "🙈 Скрыта из магазина")
        lines.append(f"🎁 {reward.title}")
        if reward.description:
            lines += ["", reward.description]
        lines += [
            "",
            f"Цена: {coins(reward.price)}",
            "В наличии: " + ("без ограничения" if reward.stock is None else f"{reward.stock} шт."),
            f"Куплено: {sold}" + (f" (ждут выдачи: {pending})" if pending else ""),
        ]
        if reward.kind == RewardKind.lottery:
            wins, paid = await shop_service.lottery_wins(session, reward.id)
            lines += [
                f"🎡 Колесо фортуны: шанс {reward.win_chance}%, выигрыш {coins(reward.prize or 0)}",
                f"Выигрышей: {wins}, выплачено {coins(paid)}",
            ]
            if reward.loss_chance:
                losses, taken = await shop_service.lottery_losses(session, reward.id)
                lines += [
                    f"💥 Критический провал: шанс {reward.loss_chance}%, "
                    f"потеря до {coins(reward.loss or 0)}",
                    f"Провалов: {losses}, списано {coins(taken)}",
                ]
        photo = [image_attachment(reward.photo)] if reward.photo else []
        return "\n".join(lines), photo + kb.reward_admin_card(reward, readonly)

    async def _reward_update(
        self, session: AsyncSession, user: User, reward_id: int, note: str, **changes
    ) -> Screen | str:
        try:
            reward = await shop_service.update_reward(session, user, reward_id, **changes)
        except ShopError as exc:
            return str(exc)
        return await self._reward_card(session, reward, note)

    @staticmethod
    def _reward_photo_prompt() -> Screen:
        return (
            "Шаг 5 из 5.\nПришлите фото награды или нажмите «Без фото».",
            kb.reward_photo_choice(),
        )

    async def _reward_step(
        self, session: AsyncSession, user: User, text: str, attachments: list[dict]
    ) -> Screen:
        data = dict(user.state_data or {})
        if user.state == ST_RW_PHOTO:
            photo = find_image(attachments)
            if photo is None:
                return self._reward_photo_prompt()
            return await self._reward_create(session, user, photo)
        if not text or text.startswith("/"):
            return "Пришлите ответ текстом или нажмите «Отмена».", kb.cancel()
        try:
            if user.state == ST_RW_TITLE:
                if len(text) > shop_service.TITLE_MAX:
                    raise ShopError(f"Слишком длинное название, максимум {shop_service.TITLE_MAX}.")
                await users_service.set_state(session, user, ST_RW_DESC, dict(data, title=text))
                return (
                    "Шаг 2 из 5.\nОпишите награду: что получит ученик и как её выдают.",
                    kb.cancel(),
                )
            if user.state == ST_RW_DESC:
                if len(text) > shop_service.DESCRIPTION_MAX:
                    raise ShopError(
                        f"Слишком длинное описание, максимум {shop_service.DESCRIPTION_MAX}."
                    )
                await users_service.set_state(
                    session, user, ST_RW_PRICE, dict(data, description=text)
                )
                return (
                    f"Шаг 3 из 5.\nЦена в монетах: число от 1 до {shop_service.PRICE_MAX}.",
                    kb.cancel(),
                )
            if user.state == ST_RW_PRICE:
                price = shop_service.parse_price(text)
                await users_service.set_state(session, user, ST_RW_STOCK, dict(data, price=price))
                return (
                    "Шаг 4 из 5.\nСколько штук доступно? Напишите число "
                    "или нажмите «Без ограничения».",
                    kb.reward_stock_choice(),
                )
            if user.state == ST_RW_STOCK:
                stock = shop_service.parse_stock(text)
                if stock == 0:
                    raise ShopError("Для новой награды нужна хотя бы 1 штука.")
                await users_service.set_state(session, user, ST_RW_PHOTO, dict(data, stock=stock))
                return self._reward_photo_prompt()
            if user.state == ST_RW_EDIT_PRICE:
                price = shop_service.parse_price(text)
                await users_service.set_state(session, user, None)
                return await self._reward_update(
                    session, user, data.get("reward_id", 0), "💰 Цена изменена.", price=price
                )
            # ST_RW_EDIT_STOCK
            stock = shop_service.parse_stock(text)
            await users_service.set_state(session, user, None)
            return await self._reward_update(
                session, user, data.get("reward_id", 0), "📦 Количество изменено.", stock=stock
            )
        except ShopError as exc:
            keyboard = (
                kb.reward_stock_choice()
                if user.state in (ST_RW_STOCK, ST_RW_EDIT_STOCK)
                else kb.cancel()
            )
            return str(exc), keyboard

    async def _reward_create(self, session: AsyncSession, user: User, photo: dict | None) -> Screen:
        data = user.state_data or {}
        await users_service.set_state(session, user, None)
        try:
            reward = await shop_service.create_reward(
                session,
                user,
                data["title"],
                data.get("description", ""),
                data["price"],
                data.get("stock"),
                photo,
            )
        except (ShopError, KeyError) as exc:
            text = str(exc) if isinstance(exc, ShopError) else "Не удалось создать награду."
            return text, kb.to_rewards()
        return await self._reward_card(session, reward, "🎉 Награда добавлена в магазин.")

    async def _shop(self, session: AsyncSession, user: User, payload: str) -> Screen | str:
        if payload == "menu:shop":
            return await self._shop_list(session, user)
        if payload.startswith("shop:mine:"):
            return await self._my_orders(session, user, max(parse_id(payload) or 0, 0))
        reward_id = parse_id(payload) or 0
        try:
            if payload.startswith("shop:open:"):
                return await self._shop_card(session, user, reward_id)
            if payload.startswith("shop:buy:"):
                reward = await shop_service.get_for_sale(session, reward_id)
                if user.balance < reward.price:
                    raise ShopError(
                        f"Не хватает монет: нужно {reward.price}, у вас {user.balance}."
                    )
                left = coins(user.balance - reward.price)
                if reward.kind == RewardKind.lottery:
                    # Ученику — только что можно выиграть и проиграть; шансы видит админ
                    risk = (
                        f"Можно и проиграть: колесо заберёт {coins(reward.loss or 0)} "
                        "(баланс не уйдёт в минус).\n"
                        if reward.loss_chance
                        else ""
                    )
                    return (
                        f"🎡 Крутить «{reward.title}» за {coins(reward.price)}?\n"
                        f"Можно выиграть {coins(reward.prize or 0)}.\n"
                        f"{risk}После вращения останется {left} (если не повезёт).",
                        kb.shop_confirm(reward.id),
                    )
                return (
                    f"Купить «{reward.title}» за {coins(reward.price)}?\n"
                    f"После покупки останется {left}.",
                    kb.shop_confirm(reward.id),
                )
            if payload.startswith("shop:buy_ok:"):
                order = await shop_service.buy(session, user, reward_id)
                await self._check_achievements(session, user)
                if order.reward.kind == RewardKind.lottery:
                    return await self._spin_result(session, user, order)
                await self._notify_handlers(session, order)
                return (
                    f"🎉 Покупка оформлена! Заказ №{order.id}: «{order.reward.title}».\n"
                    "Награду выдаст учитель или админ, вам придёт сообщение.\n"
                    f"Баланс: {coins(user.balance)}",
                    kb.after_purchase(),
                )
        except ShopError as exc:
            return str(exc)
        return "Неизвестное действие."

    async def _spin_result(self, session: AsyncSession, user: User, order) -> Screen:
        reward = order.reward
        notice = None
        if order.prize_won and order.prize_won > 0:
            text = (
                f"🎡 Колесо крутится… и…\n\n🎉 Критический успех! "
                f"Вы выиграли {coins(order.prize_won)}!\nБаланс: {coins(user.balance)}"
            )
            notice = (
                f"🎡 {user.display_name} выиграл(а) {coins(order.prize_won)} "
                f"в «{reward.title}» (вращение №{order.id})"
            )
        elif order.prize_won and order.prize_won < 0:
            text = (
                f"🎡 Колесо крутится… и…\n\n💥 Критический провал! "
                f"Колесо забрало {coins(-order.prize_won)}.\nБаланс: {coins(user.balance)}"
            )
            notice = (
                f"💥 {user.display_name}: критический провал в «{reward.title}», "
                f"списано {coins(-order.prize_won)} (вращение №{order.id})"
            )
        else:
            text = (
                f"🎡 Колесо крутится… и…\n\n😔 В этот раз не повезло. Попробуйте ещё!\n"
                f"Баланс: {coins(user.balance)}"
            )
        if notice:
            for admin in await shop_service.admins(session):
                try:
                    await self._send(admin.max_user_id, notice)
                except httpx.HTTPError:
                    log.warning("Не удалось уведомить админа %s о вращении", admin.max_user_id)
        can_repeat = reward.is_active and user.balance >= reward.price and (
            reward.stock is None or reward.stock > 0
        )
        return text, kb.after_spin(reward.id, can_repeat)

    async def _shop_list(self, session: AsyncSession, user: User) -> Screen:
        items = await shop_service.shop_items(session)
        if not items:
            return "🛒 В магазине пока пусто. Загляните позже!", kb.shop_list([], user.balance)
        text = f"🛒 Магазин наград\nБаланс: {coins(user.balance)}"
        if any(r.price > user.balance for r in items):
            text += "\n🔒 — пока не хватает монет"
        return text, kb.shop_list(items, user.balance)

    async def _shop_card(self, session: AsyncSession, user: User, reward_id: int) -> Screen:
        reward = await shop_service.get_for_sale(session, reward_id)
        lines = [f"🎁 {reward.title}"]
        if reward.description:
            lines += ["", reward.description]
        lines += ["", f"Цена: {coins(reward.price)}"]
        if reward.kind == RewardKind.lottery:
            lines.append(
                f"🎡 Можно выиграть {coins(reward.prize or 0)}. "
                "Крутить можно сколько угодно раз."
            )
            if reward.loss_chance:
                lines.append(
                    f"💥 Но можно и проиграть: колесо заберёт {coins(reward.loss or 0)} "
                    "(баланс не уйдёт в минус)."
                )
        if reward.stock is not None:
            lines.append(f"Осталось: {reward.stock} шт.")
        affordable = user.role == Role.student and user.balance >= reward.price
        if user.role == Role.student and not affordable:
            lines.append(f"Не хватает: {coins(reward.price - user.balance)}")
        photo = [image_attachment(reward.photo)] if reward.photo else []
        return "\n".join(lines), photo + kb.shop_card(reward, affordable)

    async def _my_orders(self, session: AsyncSession, user: User, page: int) -> Screen:
        items, has_more = await shop_service.my_orders(
            session, user, page * kb.ORDERS_PAGE, kb.ORDERS_PAGE
        )
        if not items and page == 0:
            return "📦 Покупок пока нет.", kb.my_orders_nav(0, False)
        lines = ["📦 Мои покупки, новые сверху", ""]
        for o in items:
            if o.prize_won is not None:
                status = (
                    f"🎉 выигрыш {coins(o.prize_won)}" if o.prize_won > 0
                    else f"💥 провал −{coins(-o.prize_won)}" if o.prize_won < 0
                    else "🎡 без выигрыша"
                )
            else:
                status = kb.ORDER_STATUS_TITLES[o.status]
            lines.append(
                f"{status} · №{o.id} «{o.reward.title}», "
                f"{coins(o.price)} · {format_day(o.created_at)}"
            )
        if page:
            lines.append(f"\nСтраница {page + 1}")
        return "\n".join(lines), kb.my_orders_nav(page, has_more)

    async def _notify_handlers(self, session: AsyncSession, order) -> None:
        student = order.user
        klass = f" ({student.school_class.name})" if student.school_class else ""
        text = (
            f"🛒 Новая покупка, заказ №{order.id}\n"
            f"{student.display_name}{klass} — «{order.reward.title}», {coins(order.price)}"
        )
        for handler in await shop_service.handlers_for(session, order):
            try:
                await self._send(handler.max_user_id, text, kb.order_notice(order.id))
            except httpx.HTTPError:
                log.warning("Не удалось уведомить о покупке %s", handler.max_user_id)

    async def _orders(self, session: AsyncSession, user: User, payload: str) -> Screen | str:
        if user.role not in (Role.teacher, Role.admin, Role.moderator):
            return "Раздел доступен учителям, модераторам и админам."
        if payload == "ord:list":
            items = await shop_service.pending_for(session, user)
            staff = user.role in (Role.admin, Role.moderator)
            if not items:
                return "📦 Наград на выдачу нет.", kb.no_orders(staff)
            return f"📦 Ждут выдачи: {len(items)}. Выберите заказ:", kb.orders_list(items, staff)
        order_id = parse_id(payload) or 0
        try:
            if payload.startswith("ord:open:"):
                order = await shop_service.get_order(session, user, order_id)
                return self._order_card(order, viewer=user)
            if payload.startswith("ord:cancel:"):
                if not shop_service.can_cancel(user):
                    return "Отменять заказы могут учитель класса и админ."
                order = await shop_service.get_order(session, user, order_id)
                if order.status != OrderStatus.pending:
                    raise ShopError("Этот заказ уже обработан.")
                return (
                    f"Отменить заказ №{order.id} «{order.reward.title}»?\n"
                    f"{order.user.display_name} получит обратно {coins(order.price)}.",
                    kb.order_cancel_confirm(order.id),
                )
            if payload.startswith("ord:issue:"):
                order = await shop_service.issue(session, user, order_id)
                if user.role in (Role.moderator, Role.admin):
                    audit.record(
                        user, "order.issue",
                        f"№{order.id} «{order.reward.title}» — {order.user.display_name}",
                    )
                note = f"🎁 Награда «{order.reward.title}» выдана! Заказ №{order.id}."
                await self._notify_student(order, note)
                return self._order_card(order, "✅ Отмечено как выданное.", user)
            if payload.startswith("ord:cancel_ok:"):
                order = await shop_service.cancel(session, user, order_id)
                note = (
                    f"↩️ Заказ №{order.id} «{order.reward.title}» отменён, "
                    f"{coins(order.price)} возвращены.\nБаланс: {coins(order.user.balance)}"
                )
                await self._notify_student(order, note)
                if user.role == Role.admin:
                    audit.record(user, "order.cancel", f"№{order.id} «{order.reward.title}»")
                return self._order_card(order, "↩️ Заказ отменён, монеты возвращены.", user)
        except ShopError as exc:
            return str(exc)
        return "Неизвестное действие."

    def _order_card(self, order, note: str = "", viewer: User | None = None) -> Screen:
        student = order.user
        lines = [note, ""] if note else []
        lines += [
            f"Заказ №{order.id}",
            f"Ученик: {student.display_name}",
        ]
        if student.school_class:
            lines.append(f"Класс: {student.school_class.name}")
        lines += [
            f"Награда: «{order.reward.title}»",
            f"Цена: {coins(order.price)}",
            f"Куплено: {format_date(order.created_at)}",
            f"Статус: {kb.ORDER_STATUS_TITLES[order.status]}",
        ]
        keyboard = (
            kb.order_actions(order.id, viewer is None or shop_service.can_cancel(viewer))
            if order.status == OrderStatus.pending
            else kb.to_orders()
        )
        return "\n".join(lines), keyboard

    async def _notify_student(self, order, text: str) -> None:
        try:
            await self._send(order.user.max_user_id, text, kb.after_purchase())
        except httpx.HTTPError:
            log.warning("Не удалось уведомить ученика %s", order.user.max_user_id)
