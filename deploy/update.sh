#!/usr/bin/env bash
# Выкладка новой версии кода (запуск от root на сервере, после git push):
#   bash /opt/aktiv/app/deploy/update.sh
# Копия базы → свежий код → зависимости → перезапуск. Миграции бот применяет сам при старте.
set -euo pipefail

# Скрипт сам обновляется при checkout: фигурные скобки заставляют bash
# прочитать его целиком до начала работы
{

BASE=/opt/aktiv
APP=$BASE/app
UV=$BASE/home/.local/bin/uv

if [ -f "$APP/aktiv.db" ] && [ -x "$APP/.venv/bin/python" ]; then
    echo "== Копия базы перед обновлением"
    sudo -u aktiv "$APP/.venv/bin/python" "$APP/deploy/backup.py"
fi

echo "== Код из репозитория"
git --git-dir="$BASE/repo.git" --work-tree="$APP" checkout -f main
chown -R aktiv:aktiv "$APP"

echo "== Зависимости"
cd "$APP"
[ -d .venv ] || sudo -u aktiv -H "$UV" venv --python 3.13 .venv
sudo -u aktiv -H "$UV" pip install --python .venv/bin/python -q -r requirements.txt

echo "== Службы"
install -m 644 deploy/*.service deploy/*.timer /etc/systemd/system/
systemctl daemon-reload
for timer in deploy/*.timer; do
    timer=$(basename "$timer")
    systemctl enable -q "$timer"
    systemctl restart "$timer"
done
if [ -f "$APP/.env" ]; then
    systemctl enable -q aktiv-bot
    systemctl restart aktiv-bot
    sleep 3
    systemctl --no-pager --lines=5 status aktiv-bot
else
    echo "Нет $APP/.env — бот не запущен"
fi
exit 0
}
