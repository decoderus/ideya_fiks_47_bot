"""Расписание плановых задач в Docker-контейнере."""
from datetime import datetime, time, timedelta, timezone

import pytest

from app import scheduler

MSK = timezone(timedelta(hours=3))


class Stop(BaseException):
    """Не Exception: планировщик перехватывает ошибки задач."""


class Clock:
    """Подставное время: sleep сдвигает часы, после limit запусков — остановка."""

    def __init__(self, start: datetime, limit: int, lag: timedelta = timedelta()):
        self.t = start
        self.limit = limit
        self.lag = lag
        self.ran: list[tuple[str, datetime]] = []

    def now(self) -> datetime:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.t += timedelta(seconds=seconds) + self.lag

    async def runner(self, job) -> int:
        self.ran.append((job.name, self.t))
        if len(self.ran) >= self.limit:
            raise Stop
        return 0


async def run(clock: Clock) -> list[tuple[str, datetime]]:
    with pytest.raises(Stop):
        await scheduler.run(now=clock.now, sleep=clock.sleep, runner=clock.runner)
    return clock.ran


def test_next_run():
    at = time(8, 0)
    assert scheduler.next_run(at, datetime(2026, 10, 20, 7, 59, tzinfo=MSK)) == datetime(
        2026, 10, 20, 8, 0, tzinfo=MSK
    )
    # Ровно в срок — уже следующий день
    assert scheduler.next_run(at, datetime(2026, 10, 20, 8, 0, tzinfo=MSK)) == datetime(
        2026, 10, 21, 8, 0, tzinfo=MSK
    )


async def test_daily_order():
    start = datetime(2026, 10, 20, 1, 0, tzinfo=MSK)
    ran = await run(Clock(start, limit=6))
    day = lambda d, h, m: datetime(2026, 10, d, h, m, tzinfo=MSK)  # noqa: E731
    assert ran == [
        ("backup", day(20, 3, 30)),
        ("reminders", day(20, 8, 0)),
        ("contests", day(20, 8, 0)),
        ("backup", day(21, 3, 30)),
        ("reminders", day(21, 8, 0)),
        ("contests", day(21, 8, 0)),
    ]


async def test_catch_up_after_start():
    # Контейнер запущен днём: копия и итоги — сразу, напоминаний — нет
    start = datetime(2026, 10, 20, 12, 0, tzinfo=MSK)
    ran = await run(Clock(start, limit=3))
    assert [name for name, _ in ran] == ["backup", "contests", "backup"]
    assert ran[2][1] == datetime(2026, 10, 21, 3, 30, tzinfo=MSK)


async def test_late_reminders_skipped():
    # Часы ушли вперёд на 2 часа (например, сервер «спал»): напоминание пропускается
    start = datetime(2026, 10, 20, 7, 0, tzinfo=MSK)
    clock = Clock(start, limit=2, lag=timedelta(hours=2))
    ran = await run(clock)
    # backup — догоняющий запуск при старте (03:30 уже прошло)
    assert [name for name, _ in ran] == ["backup", "contests"]
