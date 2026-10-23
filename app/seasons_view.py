"""Сезоны: управление датами (админ), архив итогов (все), объявление итогов."""
from sqlalchemy.ext.asyncio import AsyncSession

from app import keyboards as kb
from app.db.models import Role, User
from app.services import seasons as seasons_service
from app.services import users as users_service
from app.services.seasons import SeasonError
from app.ui import Screen, parse_id

ST_SEASON_TITLE = "season_title"
ST_SEASON_DATE = "season_date"
ST_SEASON_RENAME = "season_rename"
ST_SEASON_MOVE = "season_move"
SEASON_STATES = (ST_SEASON_TITLE, ST_SEASON_DATE, ST_SEASON_RENAME, ST_SEASON_MOVE)

MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}


def results_text(title: str, school: list, by_class: list) -> str:
    """school: [(место, имя, очки)], by_class: [(класс, имя, очки)]."""
    lines = [f"🏁 Итоги сезона «{title}»"]
    if not school:
        lines += ["", "В этом сезоне никто не заработал очков."]
        return "\n".join(lines)
    lines += ["", "🏆 Лучшие в школе:"]
    for place, name, points in school:
        lines.append(f"{MEDALS.get(place, f'{place}.')} {name} — {points}")
    if by_class:
        lines += ["", "👥 Лучшие в классах:"]
        lines += [f"{class_name}: {name} — {points}" for class_name, name, points in by_class]
    return "\n".join(lines)


def announcement(closed: seasons_service.Closed) -> str:
    text = results_text(
        closed.season.title,
        [(s.place, s.name, s.points) for s in closed.school],
        [(name, s.name, s.points) for name, s in closed.by_class],
    )
    if closed.school:
        text += (
            "\n\nПризёры получают достижения 🥇🥈🥉. Новый сезон уже начался — "
            "копите монеты, чтобы попасть в число лучших!"
        )
    return text


class SeasonsMixin:
    """Часть класса Bot: «📅 Сезоны» (админ) и архив сезонов в «Рейтинге»."""

    async def _seasons(self, session: AsyncSession, user: User, payload: str) -> Screen | str:
        if payload == "sz:archive":
            return await self._season_archive(session)
        if payload.startswith("sz:res:"):
            return await self._season_results(session, parse_id(payload) or 0)
        if payload == "menu:seasons" and user.role == Role.moderator:
            return await self._seasons_overview(session, readonly=True)
        if user.role != Role.admin:
            return "Раздел доступен только админам."
        try:
            if payload == "menu:seasons":
                return await self._seasons_overview(session)
            if payload == "sz:add":
                await users_service.set_state(session, user, ST_SEASON_TITLE, {})
                return "➕ Название нового сезона, например «I четверть 2027/28»:", kb.cancel()
            season_id = parse_id(payload) or 0
            if payload.startswith("sz:open:"):
                return await self._season_card(session, season_id)
            if payload.startswith("sz:ren:"):
                season = await seasons_service.get(session, season_id)
                await users_service.set_state(session, user, ST_SEASON_RENAME, {"id": season.id})
                return f"✏️ Новое название для «{season.title}»:", kb.cancel()
            if payload.startswith("sz:move:"):
                season = await seasons_service.get(session, season_id)
                await users_service.set_state(session, user, ST_SEASON_MOVE, {"id": season.id})
                return (
                    f"📅 Новая дата начала «{season.title}» в виде ДД.ММ.ГГГГ "
                    f"(сейчас {season.start_date:%d.%m.%Y}):",
                    kb.cancel(),
                )
            if payload.startswith("sz:del:"):
                title = await seasons_service.delete(session, season_id)
                return await self._seasons_overview(session, f"🗑 Сезон «{title}» удалён.")
        except SeasonError as exc:
            return str(exc)
        return "Неизвестное действие."

    async def _seasons_overview(
        self, session: AsyncSession, note: str = "", readonly: bool = False
    ) -> Screen:
        seasons = await seasons_service.all_seasons(session)
        current = await seasons_service.current(session)
        lines = [note, ""] if note else []
        lines += [
            "📅 Сезоны рейтинга",
            "Сезон длится до начала следующего. В первый день нового сезона в 8:00 "
            "подводятся итоги прошлого: призёры — лучшие по балансу на этот момент.",
            "",
        ]
        for s in seasons:
            mark = " ← идёт" if current.season and s.id == current.season.id else ""
            done = " ✅ итоги подведены" if s.closed_at else ""
            lines.append(f"• {s.title} — с {s.start_date:%d.%m.%Y}{mark}{done}")
        if not readonly and seasons and seasons[-1].start_date <= seasons_service.today():
            lines += ["", "⚠️ Следующий сезон не задан: добавьте его, чтобы подвести итоги текущего."]
        return "\n".join(lines), kb.seasons_overview(seasons, readonly)

    async def _season_card(self, session: AsyncSession, season_id: int, note: str = "") -> Screen:
        season = await seasons_service.get(session, season_id)
        started = season.start_date <= seasons_service.today()
        lines = [note, ""] if note else []
        lines += [f"📅 {season.title}", f"Начало: {season.start_date:%d.%m.%Y}"]
        if started:
            lines.append("Сезон уже начался: дату и удаление поменять нельзя, название — можно.")
        return "\n".join(lines), kb.season_card(season.id, editable=not started)

    async def _season_step(self, session: AsyncSession, user: User, text: str) -> Screen:
        if not text or text.startswith("/"):
            return "Пришлите ответ текстом или нажмите «Отмена».", kb.cancel()
        data = dict(user.state_data or {})
        try:
            if user.state == ST_SEASON_TITLE:
                data["title"] = text
                await users_service.set_state(session, user, ST_SEASON_DATE, data)
                return "📅 Дата начала в виде ДД.ММ.ГГГГ, например 01.09.2027:", kb.cancel()
            if user.state == ST_SEASON_DATE:
                season = await seasons_service.add(
                    session, data.get("title", ""), seasons_service.parse_date(text)
                )
                note = f"✅ Добавлен сезон «{season.title}»."
            elif user.state == ST_SEASON_RENAME:
                season = await seasons_service.rename(session, data.get("id", 0), text)
                note = f"✏️ Теперь сезон называется «{season.title}»."
            else:  # ST_SEASON_MOVE
                season = await seasons_service.move(
                    session, data.get("id", 0), seasons_service.parse_date(text)
                )
                note = f"📅 «{season.title}» начнётся {season.start_date:%d.%m.%Y}."
        except SeasonError as exc:
            return str(exc), kb.cancel()
        await users_service.set_state(session, user, None)
        return await self._seasons_overview(session, note)

    # --- архив ---

    async def _season_archive(self, session: AsyncSession) -> Screen:
        closed = await seasons_service.archive(session)
        if not closed:
            return "📜 Прошлых сезонов с итогами пока нет.", kb.season_archive([])
        return "📜 Прошлые сезоны — выберите, чтобы увидеть итоги:", kb.season_archive(closed)

    async def _season_results(self, session: AsyncSession, season_id: int) -> Screen | str:
        try:
            season = await seasons_service.get(session, season_id)
        except SeasonError as exc:
            return str(exc)
        rows = await seasons_service.results(session, season.id)
        school = [(r.place, r.full_name, r.points) for r in rows if r.class_id is None]
        by_class = [(r.class_name, r.full_name, r.points) for r in rows if r.class_id is not None]
        return results_text(season.title, school, by_class), kb.season_results_nav()
