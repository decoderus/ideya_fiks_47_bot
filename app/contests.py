"""Соревнование классов: экран в «Рейтинге» и утренние итоги.

Итоги: python -m app.contests — запускается таймером aktiv-contests.timer каждое утро
в 08:00. По понедельникам подводит итоги недели, 1-го числа — месяца; в первый день
нового сезона — итоги прошлого сезона. Рассылает ученикам, учителям и админам.
Повторный запуск за тот же период ничего не делает.
"""
import asyncio
import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import keyboards as kb
from app.db.models import ContestCriterion, ContestPeriod, Role, User
from app.keyboards import coins
from app.services import contests as contests_service
from app.ui import Screen

log = logging.getLogger("aktiv.contests")

MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}
CRITERION_TITLES = {
    ContestCriterion.total: "💰 Больше всех монет",
    ContestCriterion.average: "⚖️ Лучший средний результат (монет на ученика)",
}
PERIOD_TITLES = {ContestPeriod.week: "недели", ContestPeriod.month: "месяца"}


def _value(criterion: ContestCriterion, value: int) -> str:
    return coins(value) if criterion == ContestCriterion.total else f"{value} на ученика"


def _dates(start, end) -> str:
    return f"{start:%d.%m}–{end:%d.%m}"


def announcement(summary: contests_service.Summary) -> str:
    lines = [
        f"🏆 Итоги {PERIOD_TITLES[summary.period]} в соревновании классов "
        f"({_dates(summary.start, summary.end)})"
    ]
    if not any(summary.winners.values()):
        lines += [
            "",
            f"В этот раз ни один класс не набрал {contests_service.MIN_ACTIVE} активных "
            "участников. Выполняйте квесты вместе — и победа будет вашей!",
        ]
        return "\n".join(lines)
    for criterion, top in summary.winners.items():
        lines += ["", CRITERION_TITLES[criterion]]
        if not top:
            lines.append("—")
        for place, score in enumerate(top, start=1):
            value = score.total if criterion == ContestCriterion.total else score.average
            lines.append(f"{MEDALS[place]} {score.name} — {_value(criterion, value)}")
    lines += [
        "",
        f"Участвуют классы, где за период хотя бы {contests_service.MIN_ACTIVE} учеников "
        "выполнили квест.",
        "Награда активным участникам классов-призёров: "
        + "/".join(str(v) for v in contests_service.REWARDS[summary.period].values())
        + " монет за 1/2/3 место (одна на класс — за лучшее место). Поздравляем!",
    ]
    return "\n".join(lines)


class ContestsMixin:
    """Часть класса Bot: экран «🏁 Соревнование классов»."""

    async def _contest_brief(self, session: AsyncSession, user: User) -> list[str]:
        """Короткий блок для экрана «Рейтинг»: лидеры недели и положение своего класса."""
        now = datetime.now(timezone.utc)
        start, _ = contests_service.current_bounds(ContestPeriod.week, now)
        items = await contests_service.scores(session, start, now)
        lines = ["", "🏁 Соревнование классов, эта неделя:"]
        for criterion in ContestCriterion:
            top = contests_service.ranking(items, criterion)
            icon = CRITERION_TITLES[criterion].split(" ", 1)[0]
            name = "по сумме" if criterion == ContestCriterion.total else "в среднем"
            if top:
                leader = top[0]
                value = leader.total if criterion == ContestCriterion.total else leader.average
                lines.append(f"{icon} Лидер {name}: {leader.name} — {_value(criterion, value)}")
            else:
                lines.append(f"{icon} Лидер {name}: пока нет")
        mine = next((c for c in items if c.class_id == user.class_id), None)
        badges = await contests_service.badges(session)
        if mine is not None and mine.class_id in badges:
            lines.append(f"Ваш класс сейчас: {', '.join(badges[mine.class_id])}!")
        if mine is not None:
            need = contests_service.MIN_ACTIVE - mine.active
            status = "в борьбе ✅" if need <= 0 else f"для участия нужно ещё {need} активных"
            lines.append(f"Ваш класс: активных {mine.active} из {mine.students}, {status}")
        lines.append("Подробнее — кнопка «🏁 Соревнование классов».")
        return lines

    async def _contest(self, session: AsyncSession, user: User) -> Screen:
        now = datetime.now(timezone.utc)
        start, _ = contests_service.current_bounds(ContestPeriod.week, now)
        items = await contests_service.scores(session, start, now)
        lines = [
            "🏁 Соревнование классов",
            f"Эта неделя (с {contests_service.local_day(start):%d.%m}), итоги — в понедельник в 8:00.",
            f"Участвуют классы, где хотя бы {contests_service.MIN_ACTIVE} учеников выполнили квест.",
        ]
        eligible = sorted((c for c in items if c.eligible), key=lambda c: (-c.active, c.name))
        if eligible:
            lines.append(
                f"Сейчас допущены ({len(eligible)}): "
                + ", ".join(f"{c.name} ({c.active} акт.)" for c in eligible)
            )
        else:
            lines.append("Пока ни один класс не набрал нужного числа активных учеников.")
        for criterion in ContestCriterion:
            lines += ["", CRITERION_TITLES[criterion]]
            top = contests_service.ranking(items, criterion)[: contests_service.PRIZE_PLACES]
            if not top:
                lines.append("Пока ни один класс не допущен.")
            for place, score in enumerate(top, start=1):
                value = score.total if criterion == ContestCriterion.total else score.average
                lines.append(f"{MEDALS[place]} {score.name} — {_value(criterion, value)}")

        lines += [
            "",
            "Награда активным участникам призёров за неделю: "
            + "/".join(str(v) for v in contests_service.REWARDS[ContestPeriod.week].values())
            + " монет, за месяц: "
            + "/".join(str(v) for v in contests_service.REWARDS[ContestPeriod.month].values())
            + ".",
        ]
        badges = await contests_service.badges(session)
        holders = [
            f"{c.name} — {', '.join(badges[c.class_id])}" for c in items if c.class_id in badges
        ]
        if holders:
            lines += ["", "Сейчас носят значок:"] + holders

        mine = next((c for c in items if c.class_id == user.class_id), None)
        if mine is not None:
            status = (
                "✅ допущен"
                if mine.eligible
                else f"нужно ещё {contests_service.MIN_ACTIVE - mine.active} активных"
            )
            lines += [
                "",
                f"Ваш класс «{mine.name}»: {coins(mine.total)}, {mine.average} на ученика, "
                f"активных {mine.active} из {mine.students} — {status}.",
            ]

        for period in ContestPeriod:
            first_day, results = await contests_service.last_results(session, period)
            if first_day is None:
                continue
            lines += ["", f"Прошлые итоги {PERIOD_TITLES[period]} (с {first_day:%d.%m}):"]
            if not results:
                lines.append("призёров не было")
            for criterion, rows in results.items():
                winner = rows[0]
                lines.append(
                    f"{CRITERION_TITLES[criterion].split(' ', 1)[0]} 🥇 {winner.class_name} — "
                    f"{_value(criterion, winner.value)}"
                )
        return "\n".join(lines), kb.contest_nav()


async def _recipients(sessions) -> list[int]:
    async with sessions() as session:
        return list(
            (
                await session.scalars(
                    select(User.max_user_id).where(
                        User.full_name.is_not(None),
                        User.role.in_([Role.student, Role.teacher, Role.moderator, Role.admin]),
                    )
                )
            ).unique()
        )


async def _broadcast(bot, user_ids: list[int], text: str, attachments: list[dict]) -> int:
    from app.ui import BROADCAST_INTERVAL

    sent = 0
    for i, user_id in enumerate(user_ids):
        if i:
            await asyncio.sleep(BROADCAST_INTERVAL)
        try:
            await bot._send(user_id, text, attachments)
            sent += 1
        except httpx.HTTPError as exc:
            log.warning("Не доставлено %s: %s", user_id, exc)
    return sent


async def morning(bot, sessions, now: datetime) -> None:
    """Утренние итоги: соревнование классов (пн, 1-е) и закончившиеся сезоны."""
    from app.seasons_view import announcement as season_announcement
    from app.services import seasons as seasons_service

    local = now.astimezone(contests_service.MSK)
    due = []
    if local.weekday() == 0:
        due.append(ContestPeriod.week)
    if local.day == 1:
        due.append(ContestPeriod.month)
    for period in due:
        async with sessions() as session:
            summary = await contests_service.summarize(session, period, now)
        if summary is None:
            log.info("Итоги %s уже подведены", PERIOD_TITLES[period])
        elif not any(summary.winners.values()):
            # Без призёров не рассылаем: итоги отмечены как подведённые
            log.info("Итоги %s: призёров нет, рассылки не будет", PERIOD_TITLES[period])
        else:
            ids = await _recipients(sessions)
            sent = await _broadcast(bot, ids, announcement(summary), kb.contest_notice())
            log.info("Итоги %s: разослано %s из %s", PERIOD_TITLES[period], sent, len(ids))
            async with sessions() as session:
                prizes = await contests_service.reward(session, summary)
            for p in prizes:
                await _broadcast(
                    bot,
                    [p.student.max_user_id],
                    f"🏁 Ваш класс «{p.class_name}» — {p.place} место {PERIOD_TITLES[period]} "
                    f"в соревновании классов!\nЗа участие: +{coins(p.amount)}\n"
                    f"Баланс: {coins(p.student.balance)}",
                    kb.contest_notice(),
                )
                if p.achievement is not None:
                    await _broadcast(
                        bot,
                        [p.student.max_user_id],
                        bot._achievement_notice(p.achievement),
                        kb.to_achievements(),
                    )
            log.info("Награды %s: %s учеников", PERIOD_TITLES[period], len(prizes))

    async with sessions() as session:
        closed = await seasons_service.close_due(session)
    for c in closed:
        if not c.school:
            log.info("Сезон «%s» закрыт: очков ни у кого нет, рассылки не будет", c.season.title)
            continue
        ids = await _recipients(sessions)
        sent = await _broadcast(bot, ids, season_announcement(c), kb.season_notice(c.season.id))
        log.info("Итоги сезона «%s»: разослано %s из %s", c.season.title, sent, len(ids))
        for student, achievement in c.prizes:
            await _broadcast(
                bot,
                [student.max_user_id],
                bot._achievement_notice(achievement),
                kb.to_achievements(),
            )


async def run() -> None:
    from app.bot import Bot
    from app.config import get_settings
    from app.db.session import make_engine, make_session_factory
    from app.max_api import MaxClient

    settings = get_settings()
    engine = make_engine(settings.database_url)
    client = MaxClient(settings.bot_token, settings.max_api_base, settings.ca_bundle)
    sessions = make_session_factory(engine)
    bot = Bot(client, sessions, settings.admin_id_set, background=False)
    try:
        await morning(bot, sessions, datetime.now(timezone.utc))
    finally:
        await client.close()
        await engine.dispose()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    asyncio.run(run())


if __name__ == "__main__":
    main()
