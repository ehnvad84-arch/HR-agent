#!/usr/bin/env bash
# Обновление установки на версию vX.Y.Z (S2–S4, S9). Запуск — из корня установки:
#   bash server/scripts/update.sh v1.2.0
# Первый раз с v1.0.0, где скрипта нет, — из тега (ОБНОВЛЕНИЕ_портала.md):
#   git fetch --tags origin && git show v1.1.0:server/scripts/update.sh > /tmp/ruware-update.sh \
#     && bash /tmp/ruware-update.sh v1.1.0
# Шаги: 1 тег и замок → 2 проверки → 3 зависимости → 4 фоновые задачи (до этого портал работает) → 5 остановка
# и копия базы → 6 код, venv, миграции → 7 запуск и проверка → 8 провал 6–7: возврат прежней версии → 9 итог.
# Настройки — server/update.conf (образец update.conf.example). .env подгружается только в вызовы hr.cli.
# Прерванный запуск (обрыв SSH на шагах 6–7) доводится тем же: update.sh <тег> — ссылка venv, миграции, запуск.
# Выход: 0 — установлена (или уже стояла); 1 — отказ или возврат на прежнюю версию; 2 — ошибка запуска или настройки.
set -euo pipefail

TAG="${1:-}"
[ "$#" = 1 ] && [[ "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "использование: update.sh vX.Y.Z" >&2; exit 2; }

ROOT="${RUWARE_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null)}" \
  || { echo "update.sh: запускайте из каталога установки (копии репозитория)" >&2; exit 2; }
cd "$ROOT"
export GIT_SSH_COMMAND="${GIT_SSH_COMMAND:-ssh -o ConnectTimeout=20 -o BatchMode=yes}"
[ -n "${RUWARE_UPDATE_SELF:-}" ] && trap 'rm -f "$RUWARE_UPDATE_SELF"' EXIT

say() { printf '\n== %s\n' "$*"; }
refuse() { echo "Обновление не начато, портал работает: $1" >&2; exit 1; }
ver_gt() { [ "$1" != "$2" ] && [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | tail -n 1)" = "$1" ]; }

# 1. Тег и повторный запуск скрипта той версии, на которую обновляемся
say "1/9 версия $TAG"
git fetch --tags origin || refuse "не удалось получить версии из репозитория (сеть, ключ GitHub или тег переписан)"
git rev-parse -q --verify "refs/tags/$TAG^{commit}" >/dev/null \
  || refuse "версия $TAG не найдена в репозитории — проверьте номер: git tag --list 'v*'"
if [ -z "${RUWARE_UPDATE_REEXEC:-}" ]; then
  self="$(mktemp "${TMPDIR:-/tmp}/ruware-update.XXXXXX")"
  if git show "$TAG:server/scripts/update.sh" >"$self" 2>/dev/null; then
    exec env RUWARE_UPDATE_REEXEC=1 RUWARE_UPDATE_SELF="$self" RUWARE_ROOT="$ROOT" bash "$self" "$TAG"
  fi
  rm -f "$self"
fi
COMMIT="$(git rev-parse "$TAG^{commit}")"
echo "коммит версии $TAG: $COMMIT"

CONF="$ROOT/server/update.conf"
[ -f "$CONF" ] || { echo "update.sh: нет $CONF — заполните по образцу server/update.conf.example" >&2; exit 2; }
[ $(( 8#$(stat -c %a "$CONF") & 8#022 )) = 0 ] \
  || { echo "update.sh: $CONF могут менять другие пользователи — chmod 600 $CONF" >&2; exit 2; }
# shellcheck source=/dev/null
. "$CONF"
CHECK_URL="${CHECK_URL:-http://127.0.0.1:8000/api/version}"
START_WAIT_S="${START_WAIT_S:-60}"
JOB_WAIT_MIN="${JOB_WAIT_MIN:-30}"
JOB_POLL_S="${JOB_POLL_S:-60}"
for v in STOP_CMD START_CMD BACKUP_DIR; do
  [ -n "${!v:-}" ] || { echo "update.sh: в $CONF не задан $v" >&2; exit 2; }
done
for v in START_WAIT_S JOB_WAIT_MIN JOB_POLL_S; do
  [[ "${!v}" =~ ^[0-9]+$ ]] || { echo "update.sh: $v в $CONF — целое число" >&2; exit 2; }
done
ENV_FILE="${RUWARE_ENV:-$ROOT/server/.env}"

umask 077
mkdir -p "$BACKUP_DIR"
exec 9>"$BACKUP_DIR/.lock"
flock -n 9 || refuse "уже идёт обновление, откат или копия базы ($BACKUP_DIR/.lock)"

# hr.cli кодом из каталога $1 и python $2; .env — только в окружение этого вызова; замок детям не передаётся
hr_in() {
  local code="$1" py="$2"
  shift 2
  ( cd "$code"
    if [ -f "$ENV_FILE" ]; then set -a; . "$ENV_FILE"; set +a; fi
    "$py" -m hr.cli "$@" ) 9>&-
}

start_back() { eval "$START_CMD" 9>&- || echo "ВНИМАНИЕ: START_CMD завершился с ошибкой — запустите службы вручную" >&2; }
up() {  # отвечает ли портал: версия $1; у v1.0.0 нет /api/version — тогда главная страница
  local want="$1" i body
  for ((i = 0; i <= START_WAIT_S; i += 2)); do
    if ver_gt v1.1.0 "$want"; then
      [ "$(curl -s -o /dev/null -w '%{http_code}' "${CHECK_URL%/api/version}/" 9>&-)" = 200 ] && return 0
    else
      body="$(curl -sf "$CHECK_URL" 9>&- || true)"
      [[ "$body" =~ \"version\":\ *\"([^\"]*)\" ]] && [ "${BASH_REMATCH[1]}" = "$want" ] && return 0
    fi
    [ "$i" -lt "$START_WAIT_S" ] && sleep 2
  done
  return 1
}
# Код уже на $TAG, но прерванный запуск (обрыв SSH, kill) не довёл шаги 6–7: ссылка venv, миграции, запуск
finish_interrupted() {
  local mig_out applied
  stuck() {
    echo "Установка $TAG не доведена: $1. Портал может быть остановлен —" \
      "раздел «Если обновление прервалось» в ОБНОВЛЕНИЕ_портала.md" >&2
    exit 1
  }
  trap '' HUP
  echo "код уже на $TAG, но установка не закончена (прерванный запуск) — довожу шаги 6–7"
  [ -x "server/.venvs/$TAG/bin/python" ] || stuck "нет venv server/.venvs/$TAG"
  eval "$STOP_CMD" 9>&- || stuck "STOP_CMD завершился с ошибкой"
  ln -sfn ".venvs/$TAG" server/.venv
  mig_out="$(hr_in "$ROOT/server" "$ROOT/server/.venv/bin/python" migrate)" || { echo "$mig_out"; stuck "миграция не прошла"; }
  echo "$mig_out"
  applied="$(sed -n 's/^применено: //p' <<<"$mig_out")"
  [ "$applied" = "ничего, база актуальна" ] && applied=""
  eval "$START_CMD" 9>&- || stuck "START_CMD завершился с ошибкой"
  up "$TAG" || stuck "портал не ответил версией $TAG за $START_WAIT_S с"
  hr_in "$ROOT/server" "$ROOT/server/.venv/bin/python" release-record --kind update --to "$TAG" --commit "$COMMIT" \
    --migrations "${applied// /}" --note "доведено повторным запуском update.sh после прерывания" >/dev/null \
    || echo "предупреждение: обновление не попало в журнал версий" >&2
  git worktree remove --force ".releases/$TAG" >/dev/null 2>&1 || true
  echo "Обновление до $TAG доведено, миграции: ${applied:-новых нет}"
  exit 0
}

# 2. Проверки — портал не останавливается
say "2/9 проверки"
# свои файлы установки git не показывает и у v1.0.0, где их нет в .gitignore
exclude="$(git rev-parse --git-path info/exclude)"
mkdir -p "$(dirname "$exclude")"
for p in .venv .venvs/ .releases/ update.conf; do
  grep -qxF "$p" "$exclude" 2>/dev/null || echo "$p" >>"$exclude"
done
dirty="$(git status --porcelain)"
if [ -n "$dirty" ]; then
  echo "В коде установки есть ручные правки:" >&2
  echo "$dirty" >&2
  refuse "верните файлы (git checkout -- <файл>, новые — уберите) или перенесите настройки в .env и update.conf"
fi
[ -L server/.venv ] || refuse "server/.venv — не ссылка; сначала раздел «Первый раз» (S11) в ОБНОВЛЕНИЕ_портала.md"
PREV_LINK="$(readlink server/.venv)"
CUR="$(git describe --tags --exact-match HEAD 2>/dev/null)" && [[ "$CUR" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] \
  || refuse "текущий код не стоит на версии vX.Y.Z (git describe --tags --exact-match) — разберите вручную"
if [ "$CUR" = "$TAG" ]; then
  # до v1.1.0 update.sh не было — прерванным такой запуск быть не мог; START_WAIT_S=0 — один запрос, без ожидания
  pending=0
  ver_gt v1.1.0 "$TAG" || hr_in "$ROOT/server" "$ROOT/server/.venv/bin/python" migrate --pending >/dev/null 2>&1 \
    || pending=$?
  case "$pending" in 0|3) ;; *) refuse "версия $TAG стоит, но миграции не проверить (база недоступна?)" ;; esac
  if ver_gt v1.1.0 "$TAG" || { [ "$PREV_LINK" = ".venvs/$TAG" ] && [ "$pending" = 0 ] && START_WAIT_S=0 up "$TAG"; }; then
    echo "версия $TAG уже установлена"
    exit 0
  fi
  finish_interrupted
fi
ver_gt "$TAG" "$CUR" || refuse "$TAG старше установленной $CUR; вернуться на прошлую версию — scripts/rollback.sh"
echo "сейчас $CUR, ставим $TAG"

WT=".releases/$TAG"
git worktree remove --force "$WT" >/dev/null 2>&1 || true
rm -rf "$WT"
git worktree prune
git worktree add -q --detach "$WT" "$TAG"
TCODE="$ROOT/$WT/server"
NEW_MIGS="$(git diff --name-only HEAD "$TAG" -- server/migrations | grep '\.sql$' || true)"
COPY=""
[ -z "$NEW_MIGS" ] || { echo "новые миграции:"; echo "$NEW_MIGS" | sed 's|^server/migrations/|  |'; }

# 3. Зависимости — до остановки, своим venv на версию
say "3/9 зависимости"
VENV="$ROOT/server/.venvs/$TAG"
req_sum="$(sha256sum "$TCODE/requirements.txt" | cut -d ' ' -f 1)"
if [ -x "$VENV/bin/python" ] && [ "$(cat "$VENV/.ruware-requirements" 2>/dev/null)" = "$req_sum" ]; then
  echo "venv $TAG уже готов"
else
  rm -rf "$VENV"
  mkdir -p "$ROOT/server/.venvs"
  # venv создаётся сразу на своём месте: пути в его скриптах абсолютные
  if command -v uv >/dev/null; then
    UV_HTTP_TIMEOUT=60 uv venv -q -p 3.12 "$VENV" 9>&- \
      && UV_HTTP_TIMEOUT=60 uv pip install -q --python "$VENV/bin/python" -r "$TCODE/requirements.txt" 9>&- \
      || { rm -rf "$VENV"; refuse "зависимости $TAG не установились (PyPI или сеть) — запустите команду ещё раз"; }
  else
    python3 -m venv "$VENV" 9>&- \
      && "$VENV/bin/pip" install -q --timeout 60 -r "$TCODE/requirements.txt" 9>&- \
      || { rm -rf "$VENV"; refuse "зависимости $TAG не установились (PyPI или сеть) — запустите команду ещё раз"; }
  fi
  echo "$req_sum" >"$VENV/.ruware-requirements"
fi
TPY="$VENV/bin/python"
# место под копию — python новой версии: у неё могут быть новые зависимости
if [ -n "$NEW_MIGS" ]; then
  RUWARE_ROOT="$ROOT" RUWARE_CODE="$TCODE" RUWARE_PY="$TPY" RUWARE_ENV="$ENV_FILE" \
    bash "$TCODE/scripts/db_copy.sh" --check-space 9>&- \
    || refuse "под копию базы нет места в $BACKUP_DIR или её размер не узнать — причина выше"
fi

# 4. Фоновые задачи (сбор hh) — ждём до JOB_WAIT_MIN
say "4/9 фоновые задачи"
jobs_wait() {
  local waited=0 rc
  while :; do
    rc=0; hr_in "$TCODE" "$TPY" jobs-running || rc=$?
    case "$rc" in
      0) return 0 ;;
      4) [ $((waited * JOB_POLL_S)) -ge $((JOB_WAIT_MIN * 60)) ] && return 4
         sleep "$JOB_POLL_S"; waited=$((waited + 1)) ;;
      *) return 1 ;;
    esac
  done
}
rc=0; jobs_wait || rc=$?
[ "$rc" = 4 ] && refuse "фоновая задача идёт дольше $JOB_WAIT_MIN мин — дождитесь её конца и запустите снова"
[ "$rc" = 0 ] || refuse "не удалось проверить фоновые задачи (база недоступна?)"

# 5. Остановка и копия базы
say "5/9 остановка портала и таймеров"
abort_stopped() { trap '' HUP INT TERM; echo "Обновление не начато: $1. Портал и таймеры запускаются снова." >&2; start_back; exit 1; }
trap 'abort_stopped "прервано"' INT TERM HUP
trap 'abort_stopped "ошибка в строке $LINENO"' ERR
eval "$STOP_CMD" 9>&- || abort_stopped "STOP_CMD завершился с ошибкой"
rc=0; hr_in "$TCODE" "$TPY" jobs-running || rc=$?
[ "$rc" = 0 ] || abort_stopped "после остановки ещё идёт фоновая задача или база недоступна"
if [ -n "$NEW_MIGS" ]; then
  RUWARE_LOCK_HELD=1 RUWARE_ROOT="$ROOT" RUWARE_CODE="$TCODE" RUWARE_PY="$TPY" RUWARE_ENV="$ENV_FILE" \
    bash "$TCODE/scripts/db_copy.sh" 9>&- || abort_stopped "копия базы не записалась, прежняя копия цела"
  COPY="$BACKUP_DIR/ruware.dump"
fi

# 8. Возврат прежней версии, если 6–7 не прошли (S4)
switch_failed() {
  trap - ERR
  trap '' HUP INT TERM  # возврат доводится до конца и при повторном сигнале
  set +e
  echo "Шаг не прошёл: $1. Возвращаю $CUR." >&2
  eval "$STOP_CMD" 9>&-
  git checkout -q --detach "$CUR"
  ln -sfn "$PREV_LINK" server/.venv
  start_back
  local ok=1 note="обновление до $TAG не прошло: $1"
  up "$CUR" || { ok=0; note="$note; после возврата портал не ответил"; }
  hr_in "$TCODE" "$TPY" release-record --kind auto_rollback --from "$TAG" --to "$CUR" \
    --commit "$(git rev-parse HEAD)" --note "$note" $([ "$ok" = 1 ] || echo --failed) >/dev/null \
    || echo "предупреждение: запись не попала в журнал версий" >&2
  git worktree remove --force "$WT" >/dev/null 2>&1
  echo
  if [ "$ok" = 1 ]; then
    echo "Обновление не прошло, работает $CUR, база обновлена и совместима, копия — ${COPY:-не делалась (новых миграций не было)}"
  else
    echo "Обновление не прошло, код $CUR возвращён, но портал НЕ ОТВЕЧАЕТ — проверьте службы. Копия — ${COPY:-не делалась}"
  fi
  exit 1
}

# 6. Код, venv, миграции
say "6/9 код $TAG и миграции"
trap 'switch_failed "ошибка в строке $LINENO"' ERR
trap 'switch_failed "прервано"' INT TERM HUP
git checkout -q --detach "$TAG" || switch_failed "не переключился код"
ln -sfn ".venvs/$TAG" server/.venv
mig_out="$(hr_in "$ROOT/server" "$ROOT/server/.venv/bin/python" migrate)" || { echo "$mig_out"; switch_failed "миграция не прошла"; }
echo "$mig_out"
APPLIED="$(sed -n 's/^применено: //p' <<<"$mig_out")"
[ "$APPLIED" = "ничего, база актуальна" ] && APPLIED=""
NOTICES="$(grep -v '^применено: ' <<<"$mig_out" || true)"

# 7. Запуск и проверка
say "7/9 запуск и проверка"
eval "$START_CMD" 9>&- || switch_failed "START_CMD завершился с ошибкой"
up "$TAG" || switch_failed "портал не ответил версией $TAG за $START_WAIT_S с"
trap - ERR INT TERM
trap '' HUP  # портал уже на $TAG: итог и журнал дописываются и после обрыва SSH

# 9. Итог
say "9/9 итог"
hr_root() { hr_in "$ROOT/server" "$ROOT/server/.venv/bin/python" "$@"; }
if hr_root version 2>/dev/null | grep -q '^по журналу версий: записей нет'; then
  hr_root release-record --kind baseline --to "$CUR" --commit "$(git rev-parse "$CUR^{commit}")" \
    --note "исходная версия до первого update.sh" >/dev/null || echo "предупреждение: baseline не записан" >&2
fi
hr_root release-record --kind update --from "$CUR" --to "$TAG" --commit "$COMMIT" --migrations "${APPLIED// /}" \
  >/dev/null || echo "предупреждение: обновление не попало в журнал версий" >&2
missing="$(hr_root env-missing 2>/dev/null || echo "не удалось проверить .env")"
git worktree remove --force "$WT" >/dev/null 2>&1 || true
keep="$(basename "$PREV_LINK")"
for d in server/.venvs/*/; do
  d="${d%/}"
  case "$(basename "$d")" in "$TAG"|"$keep") ;; *) rm -rf "$d" ;; esac
done

echo "Обновление прошло: $CUR → $TAG (коммит $COMMIT)"
echo "миграции: ${APPLIED:-новых нет}"
[ -n "$NOTICES" ] && echo "$NOTICES"
echo "$missing"
echo "копия базы: ${COPY:-не делалась (новых миграций не было)}"
steps=""
for v in $(sed -n 's/^## \(v[0-9][0-9.]*\) — .*/\1/p' CHANGELOG.md 2>/dev/null); do
  { ver_gt "$v" "$CUR" && ! ver_gt "$v" "$TAG"; } || continue
  s="$(awk -v v="$v" '$0 ~ "^## " { on = ($2 == v); st = 0 } on && /^### / { st = ($0 == "### Ручные шаги"); next }
                      on && st && NF' CHANGELOG.md)"
  [ -n "$s" ] && steps+="$v:"$'\n'"$s"$'\n'
done
if [ -n "$steps" ]; then
  echo "ручные шаги из CHANGELOG.md:"
  printf '%s' "$steps"
fi
