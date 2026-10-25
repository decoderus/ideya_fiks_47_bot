# Бот «Сферум. Актив» для MAX.
#   docker build -t sferum-aktiv:1.0 .
#   docker run -d --name sferum-aktiv --env-file .env -v aktiv-data:/app/data sferum-aktiv:1.0
FROM python:3.13-slim

# TZ: время в журналах — московское (без пакета tzdata: в коде смещение МСК задано явно)
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore \
    TZ=MSK-3

WORKDIR /app

# Сначала зависимости: при правке кода этот слой берётся из кэша
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Все изменяемые данные — в одном томе /app/data: база, журнал действий, копии базы.
# SCHEDULER: напоминания, итоги соревнований и копии базы запускает сам бот (app/scheduler.py)
# DEMO: демо-режим для жюри — обезличенная база demo/aktiv-demo.db при первом запуске
# и выбор роли командой /demo. Для настоящей школы: DEMO=false, DEMO_DB= (пустая база)
ENV DATABASE_URL=sqlite+aiosqlite:////app/data/aktiv.db \
    AUDIT_LOG=/app/data/logs/audit.log \
    HEARTBEAT_FILE=/app/data/heartbeat.json \
    BACKUP_DB=/app/data/aktiv.db \
    BACKUP_DIR=/app/data/backups \
    CA_BUNDLE=/app/certs/ca-bundle.pem \
    SCHEDULER=true \
    DEMO=true \
    DEMO_DB=/app/demo/aktiv-demo.db

RUN useradd --uid 1000 --no-create-home --home-dir /app aktiv \
    && mkdir -p /app/data \
    && chown aktiv:aktiv /app/data
USER aktiv
VOLUME /app/data

# Жив ли бот: признак жизни (app/health.py) обновляется при каждом успешном обращении к MAX
HEALTHCHECK --interval=60s --timeout=10s --start-period=60s --retries=3 \
    CMD ["python", "-m", "app.health"]

# Ctrl+C вместо SIGTERM: бот дописывает начатые рассылки и закрывается сам
STOPSIGNAL SIGINT
CMD ["python", "-m", "app.main"]
