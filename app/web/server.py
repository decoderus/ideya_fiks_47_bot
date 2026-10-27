"""Веб-интерфейс: страница и API для неё (aiohttp в том же процессе, что и бот).

Что видно — по тем же правилам, что в боте:
- ученик: свой профиль, успехи из журнала, рейтинг школы и своего класса;
- учитель, модератор, админ: рейтинг школы и любого класса, «пульс» любого класса.
Данные только читаются: всё, что меняет монеты и квесты, делается в боте.
"""
import logging
from datetime import datetime, timezone
from pathlib import Path

from aiohttp import web
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Role, User
from app.keyboards import ROLE_TITLES
from app.services import classes as classes_service
from app.services import profile as profile_service
from app.services import pulse as pulse_service
from app.services import rating as rating_service
from app.services import successes as successes_service
from app.ui import MSK
from app.web import auth

log = logging.getLogger("aktiv.web")

STATIC = Path(__file__).parent / "static"
RATING_LIMIT = 50
SUCCESSES_SHOWN = 10

SESSIONS = web.AppKey("sessions", async_sessionmaker)
CONFIG = web.AppKey("config", dict)
TICKETS = web.AppKey("tickets", auth.Tickets)


def _day(moment: datetime | None) -> str | None:
    if moment is None:
        return None
    moment = moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(MSK).strftime("%d.%m.%Y")


def _error(status: int, text: str) -> web.Response:
    return web.json_response({"error": text}, status=status)


async def _current_user(request: web.Request, session: AsyncSession) -> User:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise web.HTTPUnauthorized(text="Войдите через бота")
    try:
        max_user_id = auth.check_session(request.app[CONFIG]["secret"], header[7:])
    except auth.AuthError as exc:
        raise web.HTTPUnauthorized(text=str(exc)) from None
    user = await session.scalar(select(User).where(User.max_user_id == max_user_id))
    if user is None or user.full_name is None:
        raise web.HTTPForbidden(text="Сначала зарегистрируйтесь в боте")
    return user


def _is_staff(user: User) -> bool:
    return user.role in (Role.teacher, Role.moderator, Role.admin)


# --- страница и вход ---


async def index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


async def login(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except ValueError:
        return _error(400, "Неверный запрос")
    config = request.app[CONFIG]
    try:
        if body.get("initData"):
            max_user_id = auth.validate_init_data(body["initData"], config["bot_token"])
        elif body.get("ticket"):
            max_user_id = request.app[TICKETS].consume(body["ticket"])
        else:
            return _error(401, "Откройте веб-версию из бота: кнопка «🌐 Веб-версия» в меню")
    except auth.AuthError as exc:
        return _error(401, str(exc))
    async with request.app[SESSIONS]() as session:
        user = await session.scalar(select(User).where(User.max_user_id == max_user_id))
        if user is None or user.full_name is None:
            return _error(403, "Сначала зарегистрируйтесь в боте")
    return web.json_response({"token": auth.issue_session(config["secret"], max_user_id)})


# --- данные ---


async def me(request: web.Request) -> web.Response:
    async with request.app[SESSIONS]() as session:
        user = await _current_user(request, session)
        data = {
            "name": user.display_name,
            "role": user.role.value,
            "role_title": ROLE_TITLES[user.role],
            "class": user.school_class.name if user.school_class else None,
            "classes": [c.name for c in user.teacher_classes] if user.role == Role.teacher else [],
            "staff": _is_staff(user),
        }
        if user.role == Role.student:
            p = await profile_service.build(session, user)
            data["balance"] = user.balance
            data["profile"] = {
                "school_place": p.school_place, "school_total": p.school_total,
                "class_place": p.class_place, "class_total": p.class_total,
                "quests_done": p.quests_done, "earned": p.earned, "pending": p.pending,
                "streaks": [{"title": s.title, "days": s.days, "done_today": s.done_today} for s in p.streaks],
                "achievements": p.achievements, "achievements_total": p.achievements_total,
                "recent": [{"emoji": a.emoji, "title": a.title, "date": _day(when)} for a, when in p.recent],
            }
            items, _ = await successes_service.for_student(session, user.id, 0, SUCCESSES_SHOWN)
            data["successes"] = [
                {
                    "date": _day(s.created_at),
                    "category": successes_service.BY_KEY.get(s.category, successes_service.BY_KEY["other"]).title,
                    "emoji": successes_service.BY_KEY.get(s.category, successes_service.BY_KEY["other"]).emoji,
                    "text": s.text,
                    "author": s.author.display_name,
                    "coins": s.coins,
                }
                for s in items
            ]
    return web.json_response(data)


async def rating(request: web.Request) -> web.Response:
    raw = request.query.get("class_id")
    async with request.app[SESSIONS]() as session:
        user = await _current_user(request, session)
        all_classes = await classes_service.all_classes(session)
        visible = all_classes if _is_staff(user) else [c for c in all_classes if c.id == user.class_id]
        class_id = None
        if raw:
            try:
                class_id = int(raw)
            except ValueError:
                return _error(400, "Неверный класс")
            if class_id not in {c.id for c in visible}:
                return _error(403, "Доступен рейтинг только вашего класса")
        entries = await rating_service.leaderboard(session, class_id)
    mine = next((e for e in entries if e.user_id == user.id), None)
    return web.json_response({
        "title": next((c.name for c in visible if c.id == class_id), "Вся школа"),
        "total": len(entries),
        "entries": [
            {"place": e.place, "name": e.name, "score": e.score, "me": e.user_id == user.id}
            for e in entries[:RATING_LIMIT]
        ],
        "mine": {"place": mine.place, "score": mine.score} if mine else None,
        "classes": [{"id": c.id, "name": c.name} for c in visible],
    })


async def classes(request: web.Request) -> web.Response:
    async with request.app[SESSIONS]() as session:
        user = await _current_user(request, session)
        if not successes_service.can_view(user):
            return _error(403, "Раздел доступен учителям, модераторам и админам")
        items = await classes_service.all_classes(session)
        own = {c.id for c in user.teacher_classes} if user.role == Role.teacher else set()
        counts = dict(
            (await session.execute(
                select(User.class_id, func.count()).where(User.role == Role.student, User.full_name.is_not(None))
                .group_by(User.class_id)
            )).all()
        )
    return web.json_response([
        {"id": c.id, "name": c.name, "own": c.id in own, "students": counts.get(c.id, 0)} for c in items
    ])


async def pulse(request: web.Request) -> web.Response:
    try:
        class_id = int(request.match_info["class_id"])
    except ValueError:
        return _error(400, "Неверный класс")
    async with request.app[SESSIONS]() as session:
        user = await _current_user(request, session)
        if not successes_service.can_view(user):
            return _error(403, "Раздел доступен учителям, модераторам и админам")
        p = await pulse_service.class_pulse(session, class_id)
        if p is None:
            return _error(404, "Класс не найден")
        data = {
            "class": p.school_class.name,
            "week_start": p.week_start.strftime("%d.%m"),
            "students": len(p.students),
            "active": len(p.active),
            "active_before": p.active_before,
            "eligible": p.eligible,
            "need_active": p.need_active,
            "place_total": p.place_total,
            "place_average": p.place_average,
            "successes_week": p.successes_week,
            "new_achievements": p.new_achievements,
            "streaks": [{"name": s.display_name, "quest": q, "days": d} for s, q, d in p.streaks],
            "inactive": [{"name": s.display_name, "last": _day(last)} for s, last in p.inactive],
            "need_help": [{"name": s.display_name, "quest": q} for s, q in p.need_help],
            "roster": sorted(
                ({"name": s.display_name, "active": s.id in p.active} for s in p.students),
                key=lambda r: (not r["active"], r["name"]),
            ),
        }
    return web.json_response(data)


async def health(request: web.Request) -> web.Response:
    return web.Response(text="ok")


@web.middleware
async def errors(request: web.Request, handler):
    try:
        return await handler(request)
    except web.HTTPException as exc:
        if request.path.startswith("/api/") and exc.status >= 400:
            return _error(exc.status, exc.text or exc.reason)
        raise
    except Exception:
        log.exception("Ошибка веб-интерфейса: %s", request.path)
        return _error(500, "Что-то пошло не так. Попробуйте ещё раз")


def make_app(sessions: async_sessionmaker, bot_token: str, secret: bytes, tickets: auth.Tickets) -> web.Application:
    app = web.Application(middlewares=[errors])
    app[SESSIONS] = sessions
    app[CONFIG] = {"bot_token": bot_token, "secret": secret}
    app[TICKETS] = tickets
    app.router.add_get("/", index)
    app.router.add_post("/api/login", login)
    app.router.add_get("/api/me", me)
    app.router.add_get("/api/rating", rating)
    app.router.add_get("/api/classes", classes)
    app.router.add_get("/api/pulse/{class_id}", pulse)
    app.router.add_get("/health", health)
    return app


async def start(app: web.Application, host: str, port: int) -> web.AppRunner:
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, host, port).start()
    log.info("Веб-интерфейс: http://%s:%s", host, port)
    return runner
