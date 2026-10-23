"""Проверка токена: python check_token.py"""
import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()

token = os.getenv("BOT_TOKEN")
if not token:
    sys.exit("BOT_TOKEN не найден: проверьте файл .env")

base = os.getenv("MAX_API_BASE", "https://platform-api2.max.ru")
verify = os.getenv("CA_BUNDLE") or True

try:
    r = httpx.get(f"{base}/me", headers={"Authorization": token}, timeout=10, verify=verify)
except httpx.ConnectError as exc:
    sys.exit(
        f"Не удалось подключиться: {exc}\n"
        "Если в тексте есть CERTIFICATE_VERIFY_FAILED, добавьте сертификат Минцифры "
        "в доверенные или укажите CA_BUNDLE в .env."
    )

print(r.status_code)
print(r.text)
