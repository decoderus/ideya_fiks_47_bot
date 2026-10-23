"""Плановые задачи внутри Docker-контейнера (на сервере их запускают таймеры systemd).

Включается переменной SCHEDULER=true: бот запускает расписание фоновой задачей.
Каждая задача — отдельный процесс, как при запуске таймером: сбой задачи не роняет бота.

Расписание по Москве:
- 03:30 — копия базы (deploy/backup.py);
- 08:00 — напоминания о сериях (app.reminders);
- 08:00 — итоги соревнования классов и сезонов (app.contests).
"""
import asyncio
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from app.services.contests import MSK

log = logging.getLogger("aktiv.scheduler")


@dataclass(frozen=True)
class Job:
    name: str
    at: time
    args: tuple[str, ...]
    # Запустить сразу при старте, если сегодняшний запуск уже прошёл (как Persistent у таймера).
    # Только для задач, которые сами не повторяются за тот же период.
    catch_up: bool = False
    # Опоздание больше этого — запуск пропускается (утреннее напоминание днём не нужно)
    max_delay: timedelta | None = None


JOBS = (
    Job("backup", time(3, 30), ("deploy/backup.py",), catch_up=True),
    Job("reminders", time(8, 0), ("-m", "app.reminders"), max_delay=timedelta(hours=1)),
    Job("contests", time(8, 0), ("-m", "app.contests"), catch_up=True),
)


def next_run(at: time, after: datetime) -> datetime:
    """Ближайшее время at строго позже after."""
    candidate = after.replace(hour=at.hour, minute=at.minute, second=0, microsecond=0)
    if candidate <= after:
        candidate += timedelta(days=1)
    return candidate


async def run_job(job: Job) -> int:
    log.info("Задача %s: запуск", job.name)
    proc = await asyncio.create_subprocess_exec(sys.executable, *job.args)
    code = await proc.wait()
    if code:
        log.warning("Задача %s завершилась с кодом %s", job.name, code)
    else:
        log.info("Задача %s: готово", job.name)
    return code


async def _safe(runner, job: Job) -> None:
    """Сбой запуска одной задачи не останавливает расписание."""
    try:
        await runner(job)
    except Exception:
        log.exception("Задача %s: не удалось запустить", job.name)


async def run(jobs=JOBS, now=lambda: datetime.now(MSK), sleep=asyncio.sleep, runner=run_job):
    start = now()
    for job in jobs:
        if job.catch_up and start.time() >= job.at:
            await _safe(runner, job)
    due = {job: next_run(job.at, start) for job in jobs}
    while True:
        # При равном времени — в порядке списка JOBS
        job = min(jobs, key=lambda j: due[j])
        delay = (due[job] - now()).total_seconds()
        if delay > 0:
            await sleep(delay)
        late = now() - due[job]
        if job.max_delay is not None and late > job.max_delay:
            log.warning("Задача %s пропущена: опоздание %s", job.name, late)
        else:
            await _safe(runner, job)
        due[job] = next_run(job.at, due[job])
