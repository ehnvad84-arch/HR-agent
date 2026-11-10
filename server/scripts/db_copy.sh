#!/usr/bin/env bash
# Одна свежая копия базы (S12): недельный таймер у заказчика и шаг update.sh перед новыми миграциями.
#   bash server/scripts/db_copy.sh                — ruware.dump.new → проверка → поверх ruware.dump
#   bash server/scripts/db_copy.sh --check-space  — только хватит ли места (выход 1 — мало)
# Настройки — server/update.conf (образец update.conf.example):
#   BACKUP_DIR    — каталог копии вне репозитория
#   SPACE_FACTOR  — свободно должно быть не меньше размера базы × множитель (по умолчанию 1.2)
#   PG_DUMP       — команда pg_dump 16 с базой, копия — в stdout; скрипт дописывает -Fc
#                   (pg_dump -d postgresql://…/ruware_hr  или  docker exec ruware-db pg_dump -U postgres ruware_hr)
#   PG_RESTORE    — pg_restore 16, читает копию из stdin (по умолчанию pg_restore; Docker — docker exec -i … pg_restore)
# От update.sh: RUWARE_LOCK_HELD=1 — замок BACKUP_DIR/.lock уже взят; RUWARE_ROOT — корень установки;
# RUWARE_CODE и RUWARE_PY — каталог server и python, которыми звать hr.cli (код цели); RUWARE_ENV — файл .env.
# Выход: 0 — копия записана (места хватает); 1 — мало места, копия не удалась или замок занят, прежняя копия цела;
# 2 — ошибка запуска или настройки.
set -euo pipefail

ROOT="${RUWARE_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
CONF="${RUWARE_CONF:-$ROOT/server/update.conf}"
ENV_FILE="${RUWARE_ENV:-$ROOT/server/.env}"
CODE="${RUWARE_CODE:-$ROOT/server}"
PY="${RUWARE_PY:-$ROOT/server/.venv/bin/python}"

check_only=0
case "$#:${1:-}" in
  0:) ;;
  1:--check-space) check_only=1 ;;
  *) echo "использование: db_copy.sh [--check-space]" >&2; exit 2 ;;
esac

[ -f "$CONF" ] || { echo "копия базы: нет $CONF — образец server/update.conf.example" >&2; exit 2; }
# shellcheck source=/dev/null
. "$CONF"
SPACE_FACTOR="${SPACE_FACTOR:-1.2}"
PG_RESTORE="${PG_RESTORE:-pg_restore}"
[ -n "${BACKUP_DIR:-}" ] || { echo "копия базы: в $CONF не задан BACKUP_DIR" >&2; exit 2; }
[ -n "${PG_DUMP:-}" ] || { echo "копия базы: в $CONF не задан PG_DUMP" >&2; exit 2; }
[[ "$SPACE_FACTOR" =~ ^[0-9]+([.][0-9]+)?$ ]] || { echo "копия базы: SPACE_FACTOR — число, например 1.2" >&2; exit 2; }

if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck source=/dev/null
  . "$ENV_FILE"
  set +a
fi

hr_cli() { (cd "$CODE" && "$PY" -m hr.cli "$@"); }

DUMP="$BACKUP_DIR/ruware.dump"
NEW="$DUMP.new"
umask 077
mkdir -p "$BACKUP_DIR"

# Копия записывается — итог в историю задач; не записался (база недоступна) — только предупреждение.
fail() {
  rm -f "$NEW" "$DUMP.info.new"
  echo "копия базы не записана: $1. Прежняя копия цела." >&2
  if [ "$check_only" = 0 ]; then
    hr_cli job-record db_copy --failed --error "$1" >/dev/null || echo "предупреждение: итог не попал в историю задач" >&2
  fi
  exit 1
}

size="$(hr_cli db-size)" || fail "не удалось узнать размер базы"
[[ "$size" =~ ^[0-9]+$ ]] || fail "не удалось узнать размер базы"
need="$(awk -v s="$size" -v f="$SPACE_FACTOR" 'BEGIN { printf "%.0f", s * f }')"
avail="$(df -B1 --output=avail "$BACKUP_DIR" | tail -n 1 | tr -d ' ')"
if [ "$avail" -lt "$need" ]; then
  fail "мало места в $BACKUP_DIR: свободно $((avail / 1048576)) МБ, нужно $((need / 1048576)) МБ"
fi
if [ "$check_only" = 1 ]; then
  echo "места под копию хватает: свободно $((avail / 1048576)) МБ, нужно $((need / 1048576)) МБ"
  exit 0
fi

if [ "${RUWARE_LOCK_HELD:-}" != 1 ]; then
  exec 9>"$BACKUP_DIR/.lock"
  flock -n 9 || fail "идёт обновление или другая копия ($BACKUP_DIR/.lock)"
fi

started="$(date +%s)"
eval "$PG_DUMP -Fc" >"$NEW" || fail "pg_dump завершился с ошибкой"
[ -s "$NEW" ] || fail "pg_dump записал пустой файл"
eval "$PG_RESTORE --list" <"$NEW" >/dev/null || fail "pg_restore --list не читает копию"

ver="$(git -C "$ROOT" describe --tags --exact-match HEAD 2>/dev/null || head -n 1 "$ROOT/VERSION" 2>/dev/null || echo unknown)"
[[ "$ver" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || ver=unknown
bytes="$(stat -c %s "$NEW")"
printf 'версия: %s\nвремя: %s\nразмер: %s\n' "$ver" "$(date '+%Y-%m-%d %H:%M:%S %z')" "$bytes" >"$DUMP.info.new"
mv -f "$NEW" "$DUMP"
mv -f "$DUMP.info.new" "$DUMP.info"
seconds=$(( $(date +%s) - started ))

hr_cli job-record db_copy --stats "{\"bytes\": $bytes, \"seconds\": $seconds, \"version\": \"$ver\"}" >/dev/null \
  || echo "предупреждение: итог не попал в историю задач" >&2
echo "копия базы записана: $DUMP ($((bytes / 1048576)) МБ, $ver)"
