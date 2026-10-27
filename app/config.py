from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    bot_token: str
    max_api_base: str = "https://platform-api2.max.ru"
    database_url: str = "sqlite+aiosqlite:///./aktiv.db"
    admin_ids: str = ""
    ca_bundle: str = ""
    # Журнал действий модераторов и админов (файл, ротация по размеру)
    audit_log: str = "logs/audit.log"
    # Признак жизни для HEALTHCHECK и распознавания сбоев (app/health.py)
    heartbeat_file: str = "logs/heartbeat.json"
    # Кому писать о сбоях: MAX user_id через запятую. Пусто — всем админам из базы
    alert_ids: str = ""
    # Плановые задачи внутри бота (Docker). На сервере их запускают таймеры systemd.
    scheduler: bool = False
    # Демо-режим для жюри (app/demo.py) и база, которая копируется при первом запуске
    demo: bool = False
    demo_db: str = ""
    # Веб-интерфейс (app/web): WEB_PORT=0 — выключен. WEB_URL — внешний адрес с https://
    # (для кнопки «🌐 Веб-версия» и мини-приложения MAX). WEB_SECRET — подпись пропусков и сессий,
    # пусто — выводится из токена бота.
    web_port: int = 0
    web_host: str = "127.0.0.1"
    web_url: str = ""
    web_secret: str = ""

    @field_validator("admin_ids")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()

    @property
    def admin_id_set(self) -> set[int]:
        return {int(x) for x in self.admin_ids.split(",") if x.strip()}

    @property
    def alert_id_set(self) -> set[int]:
        return {int(x) for x in self.alert_ids.split(",") if x.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
