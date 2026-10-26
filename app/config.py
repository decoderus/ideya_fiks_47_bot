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
    # Копии базы вне сервера (deploy/offsite.py): облако по WebDAV, например Яндекс Диск.
    # Пустой OFFSITE_USER — выгрузка выключена. Копии шифруются ключом BACKUP_PASSPHRASE.
    offsite_url: str = "https://webdav.yandex.ru"
    offsite_user: str = ""
    offsite_password: str = ""  # пароль приложения, не основной пароль аккаунта
    offsite_dir: str = "aktiv-backups"
    offsite_keep_days: int = 30
    backup_passphrase: str = ""

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
