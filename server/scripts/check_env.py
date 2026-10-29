"""Проверка .env перед подключением hh: что вписано и верно. Значения не печатает.

Запуск из server/: set -a; source .env; set +a; python scripts/check_env.py
"""
import os
import sys

import psycopg
from cryptography.fernet import Fernet

e = os.environ.get
ok = True


def say(good: bool, text: str) -> None:
    global ok
    ok = ok and good
    print(("  ✓ " if good else "  ✗ ") + text)


try:
    Fernet(e("SECRETS_KEY", ""))
    say(True, "SECRETS_KEY правильный")
except ValueError:
    say(False, "SECRETS_KEY пустой или неправильный: получите новый командой python -m hr.cli gen-key")
for name in ("HH_CLIENT_ID", "HH_CLIENT_SECRET"):
    v = e(name, "")
    say(bool(v) and " " not in v, f"{name} вписан" if v else f"{name} пустой")
say(e("HH_EMPLOYER_ID") == "1473839", "HH_EMPLOYER_ID = 1473839")
say(e("HH_REDIRECT_URI") == "http://localhost:8000/api/oauth/hh/callback", "HH_REDIRECT_URI правильный")
say("@" in e("HH_USER_AGENT", ""), "HH_USER_AGENT с почтой")
try:
    psycopg.connect(e("DATABASE_URL", ""), connect_timeout=5).close()
    say(True, "база отвечает")
except psycopg.Error:
    say(False, "база не отвечает: проверьте строку DATABASE_URL")
print("ШАГ 1 ВЫПОЛНЕН" if ok else "Есть ошибки: исправьте строки с ✗ в nano .env")
sys.exit(0 if ok else 1)
