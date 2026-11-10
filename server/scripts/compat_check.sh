#!/usr/bin/env bash
# Проверка совместимости N-1 (S6): тесты прошлой версии на базе новой. Запуск разработчиком из копии репозитория:
#   bash server/scripts/compat_check.sh v1.0.0
# Код и тесты прошлого тега — во временном worktree, миграции в нём заменены файлами HEAD; база —
# …/ruware_hr_compat_test на том же сервере, что DATABASE_URL (своё имя — COMPAT_DATABASE_URL, только *_compat_test).
# Фикстура прошлой версии сама пересоздаёт схемы и накатывает миграции HEAD своим db.migrate(). Законные падения
# (тест знает список миграций или значения настроек) — server/tests/compat_skip.txt (`nodeid  причина`) → --deselect.
# После — DROP DATABASE, worktree и временный каталог удаляются. pytest — через qa-test, если он есть.
# Выход: 0 — тесты прошлой версии зелёные; 1 — упали или тега нет; 2 — ошибка запуска или настройки.
set -euo pipefail

TAG="${1:-}"
[ "$#" = 1 ] && [[ "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "использование: compat_check.sh vX.Y.Z" >&2; exit 2; }

ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" \
  || { echo "compat_check.sh: запускайте из копии репозитория" >&2; exit 2; }
cd "$ROOT"
git rev-parse -q --verify "refs/tags/$TAG^{commit}" >/dev/null \
  || { echo "compat_check.sh: тега $TAG нет — git fetch --tags" >&2; exit 1; }

PY="$ROOT/server/.venv/bin/python"
[ -x "$PY" ] || { echo "compat_check.sh: нет $PY — установите зависимости (CLAUDE.md, HOW)" >&2; exit 2; }
[ -n "${DATABASE_URL:-}" ] || [ -n "${COMPAT_DATABASE_URL:-}" ] \
  || { echo "compat_check.sh: задайте DATABASE_URL (сервер тестовой базы)" >&2; exit 2; }
# имя базы заменяется на ruware_hr_compat_test; вычисляет и проверяет Python, без разбора URL в bash
DSN="$("$PY" - <<'EOF'
import os
from urllib.parse import urlparse
u = os.environ.get("COMPAT_DATABASE_URL") or urlparse(os.environ["DATABASE_URL"])._replace(
    path="/ruware_hr_compat_test").geturl()
if not urlparse(u).path.endswith("_compat_test"):
    raise SystemExit("база проверки должна оканчиваться на _compat_test")
print(u)
EOF
)" || exit 2
QA=()
command -v qa-test >/dev/null && QA=(qa-test)

SCRATCH="$(mktemp -d "${TMPDIR:-/tmp}/ruware-compat.XXXXXX")"
WT="$SCRATCH/compat"
db_admin() {  # $1 — CREATE или DROP; база служебная postgres того же сервера
  COMPAT_DSN="$DSN" "$PY" - "$1" <<'EOF'
import os
import sys
from urllib.parse import urlparse

import psycopg
from psycopg import sql

u = urlparse(os.environ["COMPAT_DSN"])
name = u.path.lstrip("/")
assert name.endswith("_compat_test"), name
with psycopg.connect(u._replace(path="/postgres").geturl(), autocommit=True) as c:
    c.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
    if sys.argv[1] == "CREATE":
        c.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
EOF
}
cleanup() {
  db_admin DROP || echo "предупреждение: база проверки не удалена" >&2
  git worktree remove --force "$WT" 2>/dev/null || true
  rm -rf "$SCRATCH"
  git worktree prune
}
trap cleanup EXIT

echo "== $TAG: код и тесты прошлой версии, миграции HEAD"
git worktree add -q --detach "$WT" "$TAG"
rm -f "$WT"/server/migrations/*.sql
cp "$ROOT"/server/migrations/*.sql "$WT/server/migrations/"
cp "$ROOT/schema_recruiting.sql" "$WT/schema_recruiting.sql"

if ! git diff --quiet "$TAG" HEAD -- server/requirements.txt; then
  echo "== requirements.txt у $TAG другой — временный venv"
  if command -v uv >/dev/null; then
    uv venv -q -p 3.12 "$SCRATCH/venv"
    uv pip install -q --python "$SCRATCH/venv/bin/python" -r "$WT/server/requirements.txt"
  else
    python3 -m venv "$SCRATCH/venv"
    "$SCRATCH/venv/bin/pip" install -q -r "$WT/server/requirements.txt"
  fi
  PY="$SCRATCH/venv/bin/python"
fi

ARGS=()
while read -r nodeid _; do
  ARGS+=(--deselect "$nodeid")
done < <(grep -v -e '^[[:space:]]*#' -e '^[[:space:]]*$' "$ROOT/server/tests/compat_skip.txt" 2>/dev/null || true)
echo "== исключено законных падений: $(( ${#ARGS[@]} / 2 )) (server/tests/compat_skip.txt)"

db_admin CREATE
rc=0
( cd "$WT/server" && DATABASE_URL="$DSN" "${QA[@]}" "$PY" -m pytest -q -p no:cacheprovider -o addopts="" \
    "${ARGS[@]}" ) || rc=$?
echo
if [ "$rc" = 0 ]; then
  echo "Совместимость: тесты $TAG зелёные на миграциях HEAD"
else
  echo "Совместимость НЕ подтверждена: тесты $TAG упали на миграциях HEAD (pytest $rc)" >&2
  exit 1
fi
