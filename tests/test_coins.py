"""Раздел «Монеты»: ручное начисление и списание с лимитами учителя."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db.models import CoinTransaction, User
from app.services import coins as coins_service
from tests.test_bot import (  # noqa: F401  env — фикстура
    ADMIN,
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


def payloads(answer) -> list[str]:
    return [b["payload"] for b in buttons(answer["attachments"])]


async def school(bot, sessions):
    """Админ, учитель (7) и ученик (5) тестового класса, у ученика 100 монет."""
    await make_teacher(bot, 7)
    await register(bot, 5)
    await award(sessions, 5, 100)
    return (await get_user(sessions, 5)).id


async def operate(bot, uid, student_id, amount, reason="За активность", give=True):
    await bot.handle_update(cb(uid, f"coins:{'give' if give else 'take'}:{student_id}"))
    await bot.handle_update(msg(uid, str(amount)))
    await bot.handle_update(msg(uid, reason))
    await bot.handle_update(cb(uid, "coins:ok"))


async def transactions(sessions) -> list[CoinTransaction]:
    async with sessions() as s:
        return list((await s.scalars(select(CoinTransaction).order_by(CoinTransaction.id))).unique())


async def test_teacher_gives_coins(env):
    bot, client, sessions = env
    sid = await school(bot, sessions)

    await bot.handle_update(cb(7, "menu:coins"))
    answer = client.answers[-1]
    assert "Тестовый класс" in answer["text"] and "осталось 1000 из 1000" in answer["text"]
    assert f"coins:st:{sid}" in payloads(answer)

    await bot.handle_update(cb(7, f"coins:st:{sid}"))
    assert "Баланс: 100 монет" in client.answers[-1]["text"]

    await bot.handle_update(cb(7, f"coins:give:{sid}"))
    assert "от 1 до 150" in client.answers[-1]["text"]
    await bot.handle_update(msg(7, "100"))
    await bot.handle_update(msg(7, "Победа в олимпиаде"))
    confirm = client.sent[-1][1]
    assert "Начислить 100 монет" in confirm and "Баланс станет: 200 монет" in confirm
    assert (await get_user(sessions, 5)).balance == 100  # до подтверждения ничего

    await bot.handle_update(cb(7, "coins:ok"))
    answer = client.answers[-1]
    assert "Начислено 100 монет" in answer["text"]
    assert "+100" in answer["text"] and "Победа в олимпиаде (Учителева Анна)" in answer["text"]
    assert "осталось 900 из 1000" in answer["text"]
    assert (await get_user(sessions, 5)).balance == 200
    to, text, _ = client.sent[-1]
    assert to == 5 and "Вам начислено 100 монет" in text and "Победа в олимпиаде" in text

    # повторное подтверждение не начисляет второй раз
    await bot.handle_update(cb(7, "coins:ok"))
    assert "уже выполнена" in client.answers[-1]["notification"]
    assert (await get_user(sessions, 5)).balance == 200


async def test_teacher_takes_coins(env):
    bot, client, sessions = env
    sid = await school(bot, sessions)
    await operate(bot, 7, sid, 40, "Опоздание", give=False)
    assert (await get_user(sessions, 5)).balance == 60
    assert "Списано 40 монет" in client.sent[-1][1]
    [t] = await transactions(sessions)
    assert (t.amount, t.reason) == (-40, "Опоздание")

    # больше, чем есть, списать нельзя
    await bot.handle_update(cb(7, f"coins:take:{sid}"))
    assert "от 1 до 60" in client.answers[-1]["text"]
    await bot.handle_update(msg(7, "61"))
    assert "только 60 монет" in client.sent[-1][1]


async def test_teacher_per_operation_limit(env):
    bot, client, sessions = env
    sid = await school(bot, sessions)
    await bot.handle_update(cb(7, f"coins:give:{sid}"))
    for bad, error in (
        ("151", "от 1 до 150"),
        ("0", "больше нуля"),
        ("-5", "больше нуля"),
        ("много", "целое число"),
    ):
        await bot.handle_update(msg(7, bad))
        assert error in client.sent[-1][1]
    await bot.handle_update(msg(7, "150"))
    assert "причину" in client.sent[-1][1]


async def test_teacher_daily_limit(env):
    bot, client, sessions = env
    sid = await school(bot, sessions)
    for _ in range(6):
        await operate(bot, 7, sid, 150)
    await operate(bot, 7, sid, 50, give=False)  # списания тоже считаются
    assert (await get_user(sessions, 5)).balance == 100 + 900 - 50

    await bot.handle_update(cb(7, f"coins:give:{sid}"))
    assert "от 1 до 50" in client.answers[-1]["text"]
    await bot.handle_update(msg(7, "51"))
    assert "сегодня можно ещё 50 из 1000" in client.sent[-1][1]
    await bot.handle_update(msg(7, "50"))
    await bot.handle_update(msg(7, "Последнее"))
    await bot.handle_update(cb(7, "coins:ok"))
    assert (await get_user(sessions, 5)).balance == 1000

    await bot.handle_update(cb(7, f"coins:give:{sid}"))
    assert "лимит исчерпан" in client.answers[-1]["notification"]

    # админа лимит учителя не касается
    await operate(bot, ADMIN, sid, 5000)
    assert (await get_user(sessions, 5)).balance == 6000


async def test_daily_limit_resets_at_moscow_midnight(env):
    bot, _, sessions = env
    sid = await school(bot, sessions)
    teacher_id = (await get_user(sessions, 7)).id
    # 23:30 по Москве = 20:30 UTC
    late = datetime(2026, 10, 18, 20, 30, tzinfo=timezone.utc)
    async with sessions() as s:
        teacher = await s.get(User, teacher_id)
        for amount in (150,) * 6 + (100,):
            await coins_service.apply(s, teacher, sid, amount, "Вечер", now=late)
        assert await coins_service.remaining_today(s, teacher, late) == 0
        # 23:59 по Москве — ещё те же сутки
        assert await coins_service.remaining_today(s, teacher, late + timedelta(minutes=29)) == 0
        # 00:01 по Москве — новые сутки
        assert await coins_service.remaining_today(s, teacher, late + timedelta(minutes=31)) == 1000


async def test_admin_no_limits_and_any_class(env):
    bot, client, sessions = env
    await school(bot, sessions)
    await register(bot, 6, "Сидоров Пётр")
    other = await add_class(sessions, "5Б", uid=6)
    sid6 = (await get_user(sessions, 6)).id

    await bot.handle_update(cb(ADMIN, "menu:coins"))
    assert f"coins:cls:{other}:0" in payloads(client.answers[-1])
    await bot.handle_update(cb(ADMIN, f"coins:cls:{other}:0"))
    assert f"coins:st:{sid6}" in payloads(client.answers[-1])
    assert "лимит" not in client.answers[-1]["text"]

    await operate(bot, ADMIN, sid6, 100_000, "Главный приз")
    assert (await get_user(sessions, 6)).balance == 100_000


async def test_teacher_only_own_class(env):
    bot, client, sessions = env
    await school(bot, sessions)
    await register(bot, 6, "Сидоров Пётр")
    other = await add_class(sessions, "5Б", uid=6)
    sid6 = (await get_user(sessions, 6)).id

    await bot.handle_update(cb(7, f"coins:cls:{other}:0"))
    assert "только ваши классы" in client.answers[-1]["notification"]
    await bot.handle_update(cb(7, f"coins:give:{sid6}"))
    assert "не найден" in client.answers[-1]["notification"]
    assert (await get_user(sessions, 6)).balance == 0


async def test_students_cannot_use_coins(env):
    bot, client, sessions = env
    sid = await school(bot, sessions)
    await bot.handle_update(cb(5, f"coins:give:{sid}"))
    assert "учителям и админам" in client.answers[-1]["notification"]
    await bot.handle_update(msg(5, "/start"))
    assert "menu:coins" not in [b["payload"] for b in buttons(client.sent[-1][2])]


async def test_cancel_operation(env):
    bot, client, sessions = env
    sid = await school(bot, sessions)
    await bot.handle_update(cb(7, f"coins:give:{sid}"))
    await bot.handle_update(msg(7, "100"))
    await bot.handle_update(msg(7, "Причина"))
    await bot.handle_update(cb(7, "cancel"))
    await bot.handle_update(cb(7, "coins:ok"))
    assert (await get_user(sessions, 5)).balance == 100
    assert await transactions(sessions) == []


# --- начисление всему классу ---


async def give_class(bot, uid, class_id, amount, reason="Победа в эстафете"):
    await bot.handle_update(cb(uid, f"coins:all:{class_id}"))
    await bot.handle_update(msg(uid, str(amount)))
    await bot.handle_update(msg(uid, reason))
    await bot.handle_update(cb(uid, "coins:ok"))


async def test_teacher_gives_whole_class(env):
    bot, client, sessions = env
    sid = await school(bot, sessions)
    await register(bot, 6, "Петров Пётр")
    cid = (await get_user(sessions, 5)).class_id

    await bot.handle_update(cb(7, "menu:coins"))
    assert f"coins:all:{cid}" in payloads(client.answers[-1])
    await bot.handle_update(cb(7, f"coins:all:{cid}"))
    assert "2 уч." in client.answers[-1]["text"] and "до 150 каждому" in client.answers[-1]["text"]
    await bot.handle_update(msg(7, "50"))
    await bot.handle_update(msg(7, "Победа в эстафете"))
    assert "по 50 монет каждому из 2 учеников" in client.sent[-1][1]
    assert "Всего: 100 монет" in client.sent[-1][1]
    await bot.handle_update(cb(7, "coins:ok"))
    assert "Начислено по 50 монет каждому из 2 учеников" in client.answers[-1]["text"]

    assert (await get_user(sessions, 5)).balance == 150
    assert (await get_user(sessions, 6)).balance == 50
    log = await transactions(sessions)
    assert [(t.amount, t.reason) for t in log] == [(50, "Победа в эстафете")] * 2
    texts = {to: text for to, text, _ in client.sent if "всему классу" in text}
    assert set(texts) == {5, 6} and "Баланс: 150 монет" in texts[5]
    assert sid  # ученик из school() тоже получил


async def test_class_give_respects_teacher_daily_limit(env):
    bot, client, sessions = env
    sid = await school(bot, sessions)
    await register(bot, 6, "Петров Пётр")
    cid = (await get_user(sessions, 5)).class_id
    for _ in range(6):
        await operate(bot, 7, sid, 150)  # потрачено 900 из 1000
    await bot.handle_update(cb(7, f"coins:all:{cid}"))
    await bot.handle_update(msg(7, "60"))  # 2 × 60 = 120 > 100
    assert "Максимум каждому: 50" in client.sent[-1][1]
    await bot.handle_update(msg(7, "50"))
    await bot.handle_update(msg(7, "За уборку"))
    await bot.handle_update(cb(7, "coins:ok"))
    assert (await get_user(sessions, 6)).balance == 50
    # дневной лимит исчерпан
    await bot.handle_update(cb(7, f"coins:all:{cid}"))
    await bot.handle_update(msg(7, "1"))
    assert "можно ещё 0" in client.sent[-1][1]


async def test_class_give_access(env):
    bot, client, sessions = env
    await school(bot, sessions)
    other = await add_class(sessions, "5Б")
    await register(bot, 6, "Чужой Ученик", school_class="5Б")
    await bot.handle_update(cb(7, f"coins:all:{other}"))
    assert "только ваши классы" in client.answers[-1]["notification"]
    # ученику недоступно
    await bot.handle_update(cb(5, f"coins:all:{other}"))
    assert "учителям и админам" in client.answers[-1]["notification"]
    # админ — без лимитов
    await give_class(bot, ADMIN, other, 5000)
    assert (await get_user(sessions, 6)).balance == 5000
