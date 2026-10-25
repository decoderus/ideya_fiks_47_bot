"""Признак жизни, HEALTHCHECK и оповещения администраторов о сбоях."""
from datetime import datetime, timedelta, timezone

import pytest

from app import health
from tests.test_bot import ADMIN, env, msg, register  # noqa: F401  env — фикстура

T0 = datetime(2026, 10, 25, 9, 0, tzinfo=timezone.utc)  # 12:00 МСК


def test_heartbeat_file(tmp_path):
    hb = health.Heartbeat(str(tmp_path / "data" / "heartbeat.json"))
    assert hb.read() is None
    hb.beat(T0)
    assert hb.read() == {"at": T0, "stopped": False}
    hb.beat(T0 + timedelta(seconds=5))  # чаще 15 с не пишет
    assert hb.read()["at"] == T0
    hb.beat(T0 + timedelta(seconds=20))
    assert hb.read()["at"] == T0 + timedelta(seconds=20)
    hb.stopped(T0 + timedelta(minutes=1))
    assert hb.read()["stopped"] is True
    (tmp_path / "data" / "heartbeat.json").write_text("мусор", encoding="utf-8")
    assert hb.read() is None


def test_is_alive():
    record = {"at": T0, "stopped": False}
    assert health.is_alive(record, T0 + timedelta(minutes=2))
    assert not health.is_alive(record, T0 + timedelta(minutes=2, seconds=1))
    assert not health.is_alive(None, T0)


def test_restart_notice():
    assert health.restart_notice(None, T0) is None  # первый запуск
    assert health.restart_notice({"at": T0, "stopped": True}, T0 + timedelta(hours=1)) is None
    text = health.restart_notice({"at": T0, "stopped": False}, T0 + timedelta(minutes=7))
    assert "перезапустился после сбоя" in text and "25.10 12:00 МСК" in text and "7 мин" in text


def test_outage_alert_only_when_long():
    dog = health.Watchdog()
    assert dog.poll_ok(T0) is None  # связь была всегда
    dog.poll_failed(T0)
    dog.poll_failed(T0 + timedelta(minutes=1))  # начало простоя не сдвигается
    assert dog.poll_ok(T0 + timedelta(minutes=2)) is None  # короче 3 минут — не беспокоим
    dog.poll_failed(T0 + timedelta(minutes=10))
    text = dog.poll_ok(T0 + timedelta(minutes=17))
    assert "MAX не отвечал боту 7 мин" in text and "с 25.10 12:10 до 25.10 12:17 МСК" in text
    assert dog.poll_ok(T0 + timedelta(minutes=18)) is None  # сообщает один раз


def test_error_alert_at_most_hourly():
    dog = health.Watchdog()
    assert "Ошибка при обработке" in dog.error("A", T0)
    assert dog.error("B", T0 + timedelta(minutes=10)) is None
    assert dog.error("C", T0 + timedelta(minutes=50)) is None
    text = dog.error("D", T0 + timedelta(hours=1, minutes=1))
    assert "всего ошибок с прошлого сообщения: 3" in text and "D" in text


async def test_alert_goes_to_all_admins(env):
    bot, client, _ = env
    await register(bot, ADMIN, "Админов Админ")
    await register(bot, 5)
    client.sent.clear()
    await bot.alert_admins("⚠️ Проверка")
    assert [to for to, text, _ in client.sent] == [ADMIN]

    bot.alert_ids = {777}  # ALERT_IDS — только им
    client.sent.clear()
    await bot.alert_admins("⚠️ Проверка")
    assert [to for to, text, _ in client.sent] == [777]


async def test_handler_error_alerts_admins_once(env, monkeypatch):
    bot, client, _ = env
    await register(bot, ADMIN, "Админов Админ")

    async def broken(*args, **kwargs):
        raise ValueError("сломалось")

    monkeypatch.setattr(bot, "_on_message", broken)
    client.sent.clear()
    for _ in range(3):
        await bot.handle_update(msg(5, "привет"))  # бот не падает
    alerts = [text for to, text, _ in client.sent if to == ADMIN]
    assert len(alerts) == 1 and "ValueError: сломалось" in alerts[0]


async def test_alert_never_raises(env, monkeypatch):
    bot, _, _ = env

    async def no_db(*args, **kwargs):
        raise RuntimeError("база недоступна")

    monkeypatch.setattr("app.services.users.admins", no_db)
    await bot.alert_admins("⚠️ Проверка")  # исключение не вылетает наружу


def test_healthcheck_exit_codes(tmp_path, monkeypatch):
    path = tmp_path / "heartbeat.json"

    class Settings:
        heartbeat_file = str(path)

    monkeypatch.setattr("app.config.get_settings", lambda: Settings)
    with pytest.raises(SystemExit) as exit_:
        health.main()
    assert exit_.value.code == 1  # файла нет — не запущен
    health.Heartbeat(str(path)).beat()
    with pytest.raises(SystemExit) as exit_:
        health.main()
    assert exit_.value.code == 0
