"""Магазин: награды (админ), покупки (ученик), выдача и отмена."""
from sqlalchemy import select

from app.db.models import Order, OrderStatus, Reward, RewardKind
from app.services import shop as shop_service
from tests.test_bot import (  # noqa: F401  env — фикстура
    ADMIN,
    PHOTO,
    add_class,
    award,
    buttons,
    cb,
    env,
    get_user,
    make_teacher,
    msg,
    register,
)


def payloads(attachments) -> list[str]:
    return [b["payload"] for b in buttons([a for a in attachments if a["type"] == "inline_keyboard"])]


async def create_reward(bot, title="Пропуск без очереди", price="100", stock=None, photo=False):
    await bot.handle_update(cb(ADMIN, "rw:new"))
    await bot.handle_update(msg(ADMIN, title))
    await bot.handle_update(msg(ADMIN, "Описание награды"))
    await bot.handle_update(msg(ADMIN, price))
    if stock is None:
        await bot.handle_update(cb(ADMIN, "rw:stock:inf"))
    else:
        await bot.handle_update(msg(ADMIN, str(stock)))
    if photo:
        await bot.handle_update(msg(ADMIN, "", attachments=PHOTO))
    else:
        await bot.handle_update(cb(ADMIN, "rw:photo:none"))


async def reward_id(sessions, title="Пропуск без очереди") -> int:
    async with sessions() as s:
        return (await s.scalar(select(Reward).where(Reward.title == title))).id


async def get_reward(sessions, rid) -> Reward:
    async with sessions() as s:
        return await s.get(Reward, rid)


async def orders(sessions) -> list[Order]:
    async with sessions() as s:
        return list((await s.scalars(select(Order).order_by(Order.id))).unique())


async def school(bot, sessions):
    """Админ, учитель тестового класса (7), ученик (5) с 300 монетами."""
    await make_teacher(bot, 7)
    await register(bot, 5)
    await award(sessions, 5, 300)


async def buy(bot, uid, rid):
    await bot.handle_update(cb(uid, f"shop:buy:{rid}"))
    await bot.handle_update(cb(uid, f"shop:buy_ok:{rid}"))


# --- награды (админ) ---


async def test_admin_creates_reward(env):
    bot, client, sessions = env
    await school(bot, sessions)
    await bot.handle_update(cb(ADMIN, "menu:rewards"))
    assert "rw:new" in payloads(client.answers[-1]["attachments"])

    await create_reward(bot, price="150", stock=3, photo=True)
    reward = await get_reward(sessions, await reward_id(sessions))
    assert (reward.price, reward.stock, reward.is_active) == (150, 3, True)
    assert reward.photo["token"] == "tok"
    _, text, att = client.sent[-1]  # после фото карточка приходит сообщением
    assert "Награда добавлена" in text and "В наличии: 3 шт." in text
    assert att[0] == {"type": "image", "payload": {"token": "tok"}}


async def test_reward_creation_validation(env):
    bot, client, sessions = env
    await school(bot, sessions)
    await bot.handle_update(cb(ADMIN, "rw:new"))
    await bot.handle_update(msg(ADMIN, "Награда"))
    await bot.handle_update(msg(ADMIN, "Описание"))
    for bad in ("0", "дорого"):
        await bot.handle_update(msg(ADMIN, bad))
        assert "Цена должна" in client.sent[-1][1]
    await bot.handle_update(msg(ADMIN, "50"))
    await bot.handle_update(msg(ADMIN, "0"))
    assert "хотя бы 1" in client.sent[-1][1]
    await bot.handle_update(msg(ADMIN, "2"))
    await bot.handle_update(msg(ADMIN, "вот фото"))  # текст вместо фото
    assert "Без фото" in [b["text"] for b in buttons(client.sent[-1][2])]
    async with sessions() as s:
        assert await s.scalar(select(Reward)) is None
    await bot.handle_update(cb(ADMIN, "rw:photo:none"))
    reward = await get_reward(sessions, await reward_id(sessions, "Награда"))
    assert (reward.price, reward.stock, reward.photo) == (50, 2, None)


async def test_only_admin_manages_rewards(env):
    bot, client, sessions = env
    await school(bot, sessions)
    await create_reward(bot)
    rid = await reward_id(sessions)
    for uid in (7, 5):
        await bot.handle_update(cb(uid, "rw:new"))
        assert "только админам" in client.answers[-1]["notification"]
        await bot.handle_update(cb(uid, f"rw:hide:{rid}"))
        assert "только админам" in client.answers[-1]["notification"]
    assert (await get_reward(sessions, rid)).is_active


async def test_admin_edits_reward(env):
    bot, client, sessions = env
    await school(bot, sessions)
    await create_reward(bot, price="100", stock=5)
    rid = await reward_id(sessions)

    await bot.handle_update(cb(ADMIN, f"rw:price:{rid}"))
    await bot.handle_update(msg(ADMIN, "80"))
    assert "Цена изменена" in client.sent[-1][1]
    await bot.handle_update(cb(ADMIN, f"rw:qty:{rid}"))
    await bot.handle_update(cb(ADMIN, "rw:stock:inf"))
    assert "без ограничения" in client.answers[-1]["text"]
    reward = await get_reward(sessions, rid)
    assert (reward.price, reward.stock) == (80, None)

    await bot.handle_update(cb(ADMIN, f"rw:hide:{rid}"))
    assert "скрыта" in client.answers[-1]["text"]
    await bot.handle_update(cb(5, "menu:shop"))
    assert "пусто" in client.answers[-1]["text"]
    await bot.handle_update(cb(5, f"shop:buy_ok:{rid}"))
    assert "больше не продаётся" in client.answers[-1]["notification"]

    await bot.handle_update(cb(ADMIN, f"rw:show:{rid}"))
    await bot.handle_update(cb(5, "menu:shop"))
    assert f"shop:open:{rid}" in payloads(client.answers[-1]["attachments"])


# --- покупка ---


async def test_student_buys_reward(env):
    bot, client, sessions = env
    await school(bot, sessions)
    await create_reward(bot, price="120", stock=2, photo=True)
    rid = await reward_id(sessions)

    await bot.handle_update(cb(5, "menu:shop"))
    answer = client.answers[-1]
    assert "Баланс: 300 монет" in answer["text"]
    await bot.handle_update(cb(5, f"shop:open:{rid}"))
    answer = client.answers[-1]
    assert "Осталось: 2 шт." in answer["text"]
    assert answer["attachments"][0]["type"] == "image"
    assert f"shop:buy:{rid}" in payloads(answer["attachments"])

    await bot.handle_update(cb(5, f"shop:buy:{rid}"))
    assert "останется 180 монет" in client.answers[-1]["text"]
    assert (await get_user(sessions, 5)).balance == 300  # без подтверждения не списано

    await bot.handle_update(cb(5, f"shop:buy_ok:{rid}"))
    assert "Покупка оформлена" in client.answers[-1]["text"]
    assert (await get_user(sessions, 5)).balance == 180
    assert (await get_reward(sessions, rid)).stock == 1
    [order] = await orders(sessions)
    assert (order.status, order.price) == (OrderStatus.pending, 120)

    # уведомление получили админ и учитель класса
    notified = {m[0] for m in client.sent if m[1].startswith("🛒 Новая покупка")}
    assert notified == {ADMIN, 7}

    await bot.handle_update(cb(5, "shop:mine:0"))
    assert "⏳ ждёт выдачи" in client.answers[-1]["text"]


async def test_not_enough_coins(env):
    bot, client, sessions = env
    await school(bot, sessions)
    await create_reward(bot, price="500")
    rid = await reward_id(sessions)

    await bot.handle_update(cb(5, "menu:shop"))
    assert any(b["text"].startswith("🔒") for b in buttons(client.answers[-1]["attachments"]))
    await bot.handle_update(cb(5, f"shop:open:{rid}"))
    answer = client.answers[-1]
    assert "Не хватает: 200 монет" in answer["text"]
    assert f"shop:buy:{rid}" not in payloads(answer["attachments"])

    await bot.handle_update(cb(5, f"shop:buy_ok:{rid}"))  # поддельная кнопка
    assert "Не хватает монет" in client.answers[-1]["notification"]
    assert (await get_user(sessions, 5)).balance == 300
    assert await orders(sessions) == []


async def test_stock_runs_out(env):
    bot, client, sessions = env
    await school(bot, sessions)
    await register(bot, 6, "Сидоров Пётр")
    await award(sessions, 6, 300)
    await create_reward(bot, price="10", stock=1)
    rid = await reward_id(sessions)

    await buy(bot, 5, rid)
    assert (await get_reward(sessions, rid)).stock == 0
    await bot.handle_update(cb(6, "menu:shop"))
    assert "пусто" in client.answers[-1]["text"]
    await buy(bot, 6, rid)
    assert "закончилась" in client.answers[-1]["notification"]
    assert (await get_user(sessions, 6)).balance == 300


async def test_only_students_buy(env):
    bot, client, sessions = env
    await school(bot, sessions)
    await create_reward(bot, price="10")
    rid = await reward_id(sessions)
    await bot.handle_update(cb(7, f"shop:buy_ok:{rid}"))
    assert "только ученики" in client.answers[-1]["notification"]


# --- выдача ---


async def test_teacher_issues_order(env):
    bot, client, sessions = env
    await school(bot, sessions)
    await create_reward(bot, price="100")
    rid = await reward_id(sessions)
    await buy(bot, 5, rid)
    [order] = await orders(sessions)

    await bot.handle_update(cb(7, "ord:list"))
    answer = client.answers[-1]
    assert "Ждут выдачи: 1" in answer["text"]
    assert f"ord:open:{order.id}" in payloads(answer["attachments"])

    await bot.handle_update(cb(7, f"ord:open:{order.id}"))
    text = client.answers[-1]["text"]
    assert "Ученик: Иванов Иван" in text and "Класс: Тестовый класс" in text

    await bot.handle_update(cb(7, f"ord:issue:{order.id}"))
    assert "выданное" in client.answers[-1]["text"]
    assert client.sent[-1][0] == 5 and "выдана" in client.sent[-1][1]
    [order] = await orders(sessions)
    assert order.status == OrderStatus.issued

    # второй раз не обработать, в списке выдачи пусто
    await bot.handle_update(cb(ADMIN, f"ord:cancel_ok:{order.id}"))
    assert "уже обработан" in client.answers[-1]["notification"]
    assert (await get_user(sessions, 5)).balance == 200
    await bot.handle_update(cb(7, "ord:list"))
    assert "нет" in client.answers[-1]["text"]

    await bot.handle_update(cb(5, "shop:mine:0"))
    assert "✅ выдано" in client.answers[-1]["text"]


async def test_cancel_refunds_coins_and_stock(env):
    bot, client, sessions = env
    await school(bot, sessions)
    await create_reward(bot, price="100", stock=1)
    rid = await reward_id(sessions)
    await buy(bot, 5, rid)
    [order] = await orders(sessions)

    await bot.handle_update(cb(ADMIN, f"ord:cancel:{order.id}"))
    assert "получит обратно 100 монет" in client.answers[-1]["text"]
    assert (await get_user(sessions, 5)).balance == 200  # без подтверждения не отменено

    await bot.handle_update(cb(ADMIN, f"ord:cancel_ok:{order.id}"))
    assert (await get_user(sessions, 5)).balance == 300
    assert (await get_reward(sessions, rid)).stock == 1
    assert client.sent[-1][0] == 5 and "возвращены" in client.sent[-1][1]
    await bot.handle_update(cb(5, "shop:mine:0"))
    assert "↩️ отменено" in client.answers[-1]["text"]


async def test_other_class_teacher_cannot_handle(env):
    bot, client, sessions = env
    await school(bot, sessions)
    await register(bot, 8, "Другая Учительница")
    await bot.handle_update(msg(ADMIN, "/role 8 teacher"))
    await add_class(sessions, "5Б", uid=8)
    await create_reward(bot, price="100")
    await buy(bot, 5, await reward_id(sessions))
    [order] = await orders(sessions)

    assert 8 not in {m[0] for m in client.sent if m[1].startswith("🛒 Новая покупка")}
    await bot.handle_update(cb(8, "ord:list"))
    assert "нет" in client.answers[-1]["text"]
    await bot.handle_update(cb(8, f"ord:issue:{order.id}"))
    assert "не найден" in client.answers[-1]["notification"]
    await bot.handle_update(cb(5, f"ord:issue:{order.id}"))
    assert "админам" in client.answers[-1]["notification"]
    [order] = await orders(sessions)
    assert order.status == OrderStatus.pending


# --- колесо фортуны ---


class FixedRng:
    """Предсказуемый генератор: random() возвращает заданные значения по очереди."""

    def __init__(self, *values):
        self.values = list(values)

    def random(self):
        return self.values.pop(0)


async def make_wheel(bot, sessions, price="200", loss=False):
    await create_reward(bot, title="Колесо Фортуны", price=price)
    rid = await reward_id(sessions, "Колесо Фортуны")
    async with sessions() as s:
        reward = await s.get(Reward, rid)
        reward.kind, reward.win_chance, reward.prize = RewardKind.lottery, 5, 10000
        if loss:
            reward.loss_chance, reward.loss = 5, 10000
        await s.commit()
    return rid


def test_spin_chance_boundary(monkeypatch):
    monkeypatch.setattr(shop_service, "rng", FixedRng(0.0, 0.0499, 0.05, 0.99))
    assert [shop_service.spin(5) for _ in range(4)] == [True, True, False, False]


def test_spin_is_about_five_percent():
    wins = sum(shop_service.spin(5) for _ in range(100_000))
    assert 4_500 < wins < 5_500


async def test_wheel_win(env, monkeypatch):
    bot, client, sessions = env
    await school(bot, sessions)
    rid = await make_wheel(bot, sessions)
    monkeypatch.setattr(shop_service, "rng", FixedRng(0.0))

    await bot.handle_update(cb(5, f"shop:open:{rid}"))
    answer = client.answers[-1]
    assert "Можно выиграть 10000 монет" in answer["text"]
    assert "%" not in answer["text"]  # шансы ученику не показываем
    keyboard = [a for a in answer["attachments"] if a["type"] == "inline_keyboard"]
    assert any(b["text"] == "🎡 Крутить за 200" for b in buttons(keyboard))
    await bot.handle_update(cb(5, f"shop:buy:{rid}"))
    assert "Можно выиграть 10000 монет" in client.answers[-1]["text"]
    assert "%" not in client.answers[-1]["text"]

    sent_before = len(client.sent)
    await bot.handle_update(cb(5, f"shop:buy_ok:{rid}"))
    answer = client.answers[-1]
    assert "🎉 Критический успех!" in answer["text"] and "Баланс: 10100 монет" in answer["text"]
    assert f"shop:buy:{rid}" in payloads(answer["attachments"])  # «Крутить ещё»
    assert (await get_user(sessions, 5)).balance == 300 - 200 + 10000

    [order] = await orders(sessions)
    assert (order.status, order.prize_won) == (OrderStatus.issued, 10000)
    new = client.sent[sent_before:]
    assert [m[0] for m in new] == [ADMIN] and "выиграл(а) 10000 монет" in new[0][1]

    await bot.handle_update(cb(5, "shop:mine:0"))
    assert "🎉 выигрыш 10000 монет" in client.answers[-1]["text"]
    await bot.handle_update(cb(7, "ord:list"))
    assert "нет" in client.answers[-1]["text"]  # выдавать нечего


async def test_wheel_lose_and_spin_again(env, monkeypatch):
    bot, client, sessions = env
    await school(bot, sessions)
    await award(sessions, 5, 300)  # всего 600
    rid = await make_wheel(bot, sessions)
    monkeypatch.setattr(shop_service, "rng", FixedRng(0.5, 0.9, 0.99))
    sent_before = len(client.sent)

    for left, can_repeat in ((400, True), (200, True), (0, False)):
        await buy(bot, 5, rid)
        answer = client.answers[-1]
        assert "не повезло" in answer["text"] and f"Баланс: {left} монет" in answer["text"]
        assert (f"shop:buy:{rid}" in payloads(answer["attachments"])) is can_repeat

    assert client.sent[sent_before:] == []  # никого не уведомляем
    assert [o.prize_won for o in await orders(sessions)] == [0, 0, 0]
    assert (await get_reward(sessions, rid)).stock is None

    await buy(bot, 5, rid)  # монет больше нет
    assert "Не хватает монет" in client.answers[-1]["notification"]
    await bot.handle_update(cb(5, "shop:mine:0"))
    assert client.answers[-1]["text"].count("🎡 без выигрыша") == 3


async def test_wheel_stats_for_admin(env, monkeypatch):
    bot, client, sessions = env
    await school(bot, sessions)
    await award(sessions, 5, 100)
    rid = await make_wheel(bot, sessions)
    monkeypatch.setattr(shop_service, "rng", FixedRng(0.99, 0.01))
    await buy(bot, 5, rid)
    await buy(bot, 5, rid)
    await bot.handle_update(cb(ADMIN, f"rw:open:{rid}"))
    text = client.answers[-1]["text"]
    assert "шанс 5%, выигрыш 10000 монет" in text
    assert "Выигрышей: 1, выплачено 10000 монет" in text


async def test_rewards_sorted_by_price(env):
    bot, client, sessions = env
    await school(bot, sessions)
    for title, price in (("Дорогая", "900"), ("Дешёвая", "10"), ("Средняя", "300"), ("Скрытая", "5")):
        await create_reward(bot, title=title, price=price)
    await bot.handle_update(cb(ADMIN, f"rw:hide:{await reward_id(sessions, 'Скрытая')}"))

    await bot.handle_update(cb(ADMIN, "menu:rewards"))
    labels = [b["text"] for b in buttons(client.answers[-1]["attachments"]) if "·" in b["text"]]
    assert labels == ["Дешёвая · 10", "Средняя · 300", "Дорогая · 900", "🙈 Скрытая · 5"]

    await bot.handle_update(cb(5, "menu:shop"))
    labels = [b["text"] for b in buttons(client.answers[-1]["attachments"]) if "·" in b["text"]]
    assert labels == ["Дешёвая · 10", "Средняя · 300", "🔒 Дорогая · 900"]


def test_roll_bands(monkeypatch):
    """5% — успех, следующие 5% — провал, остальное — ничего."""
    monkeypatch.setattr(shop_service, "rng", FixedRng(0.0499, 0.05, 0.0999, 0.1, 0.07))
    W, L, N = shop_service.WIN, shop_service.LOSS, shop_service.NOTHING
    assert [shop_service.roll(5, 5) for _ in range(4)] == [W, L, L, N]
    assert shop_service.roll(5) == N  # без провала полоса 5–10% — просто «не повезло»


async def test_wheel_critical_failure(env, monkeypatch):
    bot, client, sessions = env
    await school(bot, sessions)
    await award(sessions, 5, 19_700)  # всего 20 000
    rid = await make_wheel(bot, sessions, loss=True)

    await bot.handle_update(cb(5, f"shop:open:{rid}"))
    assert "можно и проиграть: колесо заберёт 10000 монет" in client.answers[-1]["text"]
    await bot.handle_update(cb(5, f"shop:buy:{rid}"))
    assert "Можно и проиграть: колесо заберёт 10000 монет" in client.answers[-1]["text"]
    assert "%" not in client.answers[-1]["text"]

    monkeypatch.setattr(shop_service, "rng", FixedRng(0.07))
    sent_before = len(client.sent)
    await bot.handle_update(cb(5, f"shop:buy_ok:{rid}"))
    answer = client.answers[-1]
    assert "💥 Критический провал! Колесо забрало 10000 монет." in answer["text"]
    assert (await get_user(sessions, 5)).balance == 20_000 - 200 - 10_000
    [order] = await orders(sessions)
    assert order.prize_won == -10_000
    new = client.sent[sent_before:]
    assert [m[0] for m in new] == [ADMIN] and "критический провал" in new[0][1]

    await bot.handle_update(cb(5, "shop:mine:0"))
    assert "💥 провал −10000 монет" in client.answers[-1]["text"]
    await bot.handle_update(cb(ADMIN, f"rw:open:{rid}"))
    assert "Провалов: 1, списано 10000 монет" in client.answers[-1]["text"]


async def test_wheel_failure_does_not_go_negative(env, monkeypatch):
    bot, client, sessions = env
    await school(bot, sessions)  # у ученика 300
    rid = await make_wheel(bot, sessions, loss=True)
    monkeypatch.setattr(shop_service, "rng", FixedRng(0.07))
    await buy(bot, 5, rid)
    assert "Колесо забрало 100 монет" in client.answers[-1]["text"]
    assert (await get_user(sessions, 5)).balance == 0
    [order] = await orders(sessions)
    assert order.prize_won == -100
