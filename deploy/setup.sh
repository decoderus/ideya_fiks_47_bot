#!/usr/bin/env bash
# Первичная настройка сервера Ubuntu 24.04 для бота. Запуск от root, можно повторять.
#   bash setup.sh
set -euo pipefail

BASE=/opt/aktiv

echo "== Система: обновления, пакеты, часовой пояс"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get upgrade -yq
apt-get install -yq git curl ufw unattended-upgrades sqlite3
timedatectl set-timezone Europe/Moscow
dpkg-reconfigure -f noninteractive unattended-upgrades

echo "== Подкачка 1 ГБ (страховка при установке пакетов)"
if ! swapon --show | grep -q /swapfile; then
    fallocate -l 1G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "== Журналы: не больше 200 МБ"
mkdir -p /etc/systemd/journald.conf.d
printf '[Journal]\nSystemMaxUse=200M\n' > /etc/systemd/journald.conf.d/aktiv.conf
systemctl restart systemd-journald

echo "== SSH: только по ключу"
# 00- — чтобы файл читался раньше 50-cloud-init.conf: у sshd побеждает первое значение
cat > /etc/ssh/sshd_config.d/00-aktiv.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
EOF
mkdir -p /run/sshd  # без неё sshd -t падает, пока ssh запускается по сокету
sshd -t
systemctl reload ssh

echo "== Брандмауэр: открыт только SSH"
ufw allow OpenSSH
ufw --force enable

echo "== Пользователь и папки"
id aktiv >/dev/null 2>&1 || useradd --system --create-home --home-dir "$BASE/home" --shell /usr/sbin/nologin aktiv
mkdir -p "$BASE/app" "$BASE/backups"
[ -d "$BASE/repo.git" ] || git init --bare -q -b main "$BASE/repo.git"
chown -R aktiv:aktiv "$BASE/app" "$BASE/backups" "$BASE/home"

echo "== uv и Python 3.13"
if [ ! -x "$BASE/home/.local/bin/uv" ]; then
    sudo -u aktiv -H sh -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'
fi

echo "Готово. Дальше: git push на сервер и deploy/update.sh"
