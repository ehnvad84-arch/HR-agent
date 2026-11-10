#!/usr/bin/env bash
# Сборка версии разработчиком (S6, S10). Запуск из копии репозитория на ветке фичи, раздел версии в CHANGELOG.md
# уже написан и закоммичен:
#   bash server/scripts/release.sh v1.2.0 [--dry-run]
# Шаги: 1 раздел CHANGELOG → 2 дерево и теги → 3 VERSION → 4 ruff → 5 pytest (qa-test, DATABASE_URL на *_test)
# → 6 pip-audit → 7 compat_check.sh с прошлым тегом → 8 замок новых миграций → 9 коммит.
# --dry-run проходит все проверки и возвращает VERSION и замок как были, без коммита.
# Тег ставится на main после мержа: git tag vX.Y.Z && git push origin vX.Y.Z — скрипт его не ставит.
# Выход: 0 — готово; 1 — проверка не прошла (дерево возвращено как было); 2 — ошибка запуска.
set -euo pipefail

TAG="${1:-}"
DRY=0
[ "${2:-}" = --dry-run ] && DRY=1
[[ "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] && { [ "$#" = 1 ] || { [ "$DRY" = 1 ] && [ "$#" = 2 ]; }; } \
  || { echo "использование: release.sh vX.Y.Z [--dry-run]" >&2; exit 2; }

ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" \
  || { echo "release.sh: запускайте из копии репозитория" >&2; exit 2; }
cd "$ROOT"
BIN="$ROOT/server/.venv/bin"
[ -x "$BIN/python" ] || { echo "release.sh: нет $BIN/python — установите зависимости (CLAUDE.md, HOW)" >&2; exit 2; }
tool() { if [ -x "$BIN/$1" ]; then echo "$BIN/$1"; else command -v "$1" || echo "$1"; fi; }
QA=()
command -v qa-test >/dev/null && QA=(qa-test)

say() { printf '\n== %s\n' "$*"; }
fail() { echo "Выпуск $TAG не собран: $1" >&2; exit 1; }
ver_gt() { [ "$1" != "$2" ] && [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | tail -n 1)" = "$1" ]; }

# новые миграции — файлы вне замка RELEASED.sha256; проверки — те же функции, что в tests/test_release.py
NEW="$(cd server && "$BIN/python" -c '
import sys
sys.path[:0] = [".", "tests"]
from hr import db
from test_release import released
print(" ".join(f.name for f in db.migration_files() if f.name not in released(db.MIGRATIONS_DIR)))
')" || exit 2

say "1/9 раздел $TAG в CHANGELOG.md"
probs="$(cd server && "$BIN/python" -c '
import sys
sys.path[:0] = [".", "tests"]
from hr import version
from test_release import changelog_problems
text = (version.ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
print("\n".join(changelog_problems(text, sys.argv[1], set(sys.argv[2:]))))
' "$TAG" $NEW)" || exit 2
if [ -n "$probs" ]; then
  echo "$probs" >&2
  fail "нет готового раздела в CHANGELOG.md (S10): сверху «## $TAG — ГГГГ-ММ-ДД» и пять подразделов"
fi
echo "раздел есть; новые миграции: ${NEW:-нет}"

say "2/9 дерево и теги"
dirty="$(git status --porcelain)"
[ -z "$dirty" ] || { echo "$dirty" >&2; fail "в дереве незакоммиченные изменения — закоммитьте или уберите"; }
git rev-parse -q --verify "refs/tags/$TAG" >/dev/null && fail "тег $TAG уже есть — номер версии занят"
PREV=""
for t in $(git tag --list 'v*' | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | sort -V); do
  ver_gt "$TAG" "$t" || fail "$TAG не новее выпущенной $t"
  PREV="$t"
done
CUR="$(tr -d '[:space:]' <VERSION 2>/dev/null || true)"
[ -z "$CUR" ] || [ "$CUR" = "$TAG" ] || ver_gt "$TAG" "$CUR" || fail "$TAG старше VERSION ($CUR)"
echo "прошлый тег: ${PREV:-нет}; VERSION: ${CUR:-нет}"

# всё, что скрипт меняет, возвращается, если не дошли до коммита
LOCK=server/migrations/RELEASED.sha256
committed=0
restore() { [ "$committed" = 1 ] || git checkout -q -- VERSION "$LOCK" 2>/dev/null || true; }
trap restore EXIT

say "3/9 VERSION"
echo "$TAG" >VERSION
echo "VERSION = $TAG"

say "4/9 ruff"
( cd server && "$(tool ruff)" check . ) || fail "ruff нашёл ошибки"

say "5/9 pytest"
( cd server && "${QA[@]}" "$BIN/python" -m pytest -q -o addopts="" ) || fail "тесты упали"

say "6/9 pip-audit"
( cd server && "$(tool pip-audit)" -r requirements.txt ) || fail "pip-audit нашёл уязвимости или не дошёл до базы"

say "7/9 совместимость с прошлой версией"
if [ -n "$PREV" ]; then
  bash server/scripts/compat_check.sh "$PREV" || fail "тесты $PREV не прошли на миграциях $TAG (S6)"
else
  echo "прошлого тега нет — проверка совместимости не нужна"
fi

say "8/9 замок миграций"
for m in $NEW; do
  ( cd server/migrations && sha256sum "$m" ) >>"$LOCK"
  echo "в замок: $m"
done
[ -n "$NEW" ] || echo "новых миграций нет"

say "9/9 коммит"
if [ "$DRY" = 1 ]; then
  git diff --stat -- VERSION "$LOCK"
  echo "проверка без коммита (--dry-run): все шаги прошли, VERSION и замок возвращены"
  exit 0
fi
git add VERSION "$LOCK"
if git diff --cached --quiet; then
  echo "VERSION и замок уже на $TAG — коммит не нужен"
else
  git commit -q -m "Выпуск $TAG: VERSION и замок миграций" -- VERSION "$LOCK"
  echo "коммит $(git rev-parse --short HEAD)"
fi
committed=1
echo
echo "Выпуск $TAG собран. Дальше: мерж в main, на main — git tag $TAG && git push origin $TAG"
