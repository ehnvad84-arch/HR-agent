#!/usr/bin/env bash
# Откат на прошлую версию по решению людей (S5). Запуск — из корня установки:
#   bash server/scripts/rollback.sh v1.1.0
# Возвращается только код и venv версии; база не меняется (migrate не запускается): прошлая версия работает на базе
# новой, всё, что сделали рекрутеры после обновления, остаётся. Отказ — цель ниже v1.1.0, старше версии, с которой было
# последнее удачное обновление, или пропускаемая версия в CHANGELOG.md помечена «Откат: нет».
# Шаги: 1 тег и замок → 2 проверки → 3 venv цели → 4 фоновые задачи (до этого портал работает) → 5 журнал версий
# → 6 остановка, код, venv → 7 запуск и проверка; провал 6–7 — возврат текущей версии.
# Прерванный запуск (обрыв SSH на шагах 6–7) доводится тем же: rollback.sh <тег>.
# Настройки — server/update.conf, как у update.sh. Выход: 0 — откат прошёл (или версия уже стоит); 1 — отказ или
# возврат текущей версии; 2 — ошибка запуска или настройки.
set -euo pipefail

TAG="${1:-}"
[ "$#" = 1 ] && [[ "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "использование: rollback.sh vX.Y.Z" >&2; exit 2; }

ROOT="${RUWARE_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null)}" \
  || { echo "rollback.sh: запускайте из каталога установки (копии репозитория)" >&2; exit 2; }
cd "$ROOT"
export GIT_SSH_COMMAND="${GIT_SSH_COMMAND:-ssh -o ConnectTimeout=20 -o BatchMode=yes}"

say() { printf '\n== %s\n' "$*"; }
refuse() { echo "Откат не начат, портал работает: $1" >&2; exit 1; }
ver_gt() { [ "$1" != "$2" ] && [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | tail -n 1)" = "$1" ]; }

# 1. Тег — без повторного запуска из тега: откат идёт скриптом текущей версии
say "1/7 версия $TAG"
git fetch --tags origin 2>/dev/null || echo "сеть или ключ GitHub недоступны — откат по локальным тегам"
git rev-parse -q --verify "refs/tags/$TAG^{commit}" >/dev/null \
  || refuse "версия $TAG не найдена в репозитории — проверьте номер: git tag --list 'v*'"
COMMIT="$(git rev-parse "$TAG^{commit}")"

CONF="$ROOT/server/update.conf"
[ -f "$CONF" ] || { echo "rollback.sh: нет $CONF — заполните по образцу server/update.conf.example" >&2; exit 2; }
[ $(( 8#$(stat -c %a "$CONF") & 8#022 )) = 0 ] \
  || { echo "rollback.sh: $CONF могут менять другие пользователи — chmod 600 $CONF" >&2; exit 2; }
# shellcheck source=/dev/null
. "$CONF"
CHECK_URL="${CHECK_URL:-http://127.0.0.1:8000/api/version}"
START_WAIT_S="${START_WAIT_S:-60}"
JOB_WAIT_MIN="${JOB_WAIT_MIN:-30}"
JOB_POLL_S="${JOB_POLL_S:-60}"
for v in STOP_CMD START_CMD BACKUP_DIR; do
  [ -n "${!v:-}" ] || { echo "rollback.sh: в $CONF не задан $v" >&2; exit 2; }
done
for v in START_WAIT_S JOB_WAIT_MIN JOB_POLL_S; do
  [[ "${!v}" =~ ^[0-9]+$ ]] || { echo "rollback.sh: $v в $CONF — целое число" >&2; exit 2; }
done
ENV_FILE="${RUWARE_ENV:-$ROOT/server/.env}"

umask 077
mkdir -p "$BACKUP_DIR"
exec 9>"$BACKUP_DIR/.lock"
flock -n 9 || refuse "уже идёт обновление, откат или копия базы ($BACKUP_DIR/.lock)"

# hr.cli кодом установки; .env — только в окружение этого вызова; замок детям не передаётся
hr_root() {
  ( cd "$ROOT/server"
    if [ -f "$ENV_FILE" ]; then set -a; . "$ENV_FILE"; set +a; fi
    "$ROOT/server/.venv/bin/python" -m hr.cli "$@" ) 9>&-
}

# обрыв SSH: терминала больше нет, запись в него — ошибка EIO; вывод дальше — в журнал рядом с копией базы
HUP_LOG="$BACKUP_DIR/update.log"
on_hup() { exec >>"$HUP_LOG" 2>&1; echo "== $(date '+%F %T') обрыв SSH, вывод продолжается здесь"; }
start_back() { eval "$START_CMD" 9>&- || echo "ВНИМАНИЕ: START_CMD завершился с ошибкой — запустите службы вручную" >&2; }
up() {  # отвечает ли портал версией $1 (обе версии ≥ v1.1.0 — /api/version есть)
  local want="$1" i body
  for ((i = 0; i <= START_WAIT_S; i += 2)); do
    body="$(curl -sf "$CHECK_URL" 9>&- || true)"
    [[ "$body" =~ \"version\":\ *\"([^\"]*)\" ]] && [ "${BASH_REMATCH[1]}" = "$want" ] && return 0
    [ "$i" -lt "$START_WAIT_S" ] && sleep 2
  done
  return 1
}
# Код уже на $TAG, но прерванный запуск (обрыв SSH, kill) не довёл шаги 6–7: ссылка venv и запуск
finish_interrupted() {
  stuck() {
    echo "Откат на $TAG не доведён: $1. Портал может быть остановлен —" \
      "раздел «Если обновление прервалось» в ОБНОВЛЕНИЕ_портала.md" >&2
    exit 1
  }
  trap on_hup HUP
  echo "код уже на $TAG, но откат не закончен (прерванный запуск) — довожу шаги 6–7"
  [ -x "server/.venvs/$TAG/bin/python" ] || stuck "нет venv server/.venvs/$TAG"
  eval "$STOP_CMD" 9>&- || stuck "STOP_CMD завершился с ошибкой"
  ln -sfn ".venvs/$TAG" server/.venv
  eval "$START_CMD" 9>&- || stuck "START_CMD завершился с ошибкой"
  up "$TAG" || stuck "портал не ответил версией $TAG за $START_WAIT_S с"
  echo "Откат на $TAG доведён, база не менялась"
  exit 0
}

# 2. Проверки — портал не останавливается
say "2/7 проверки"
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
  # ниже v1.1.0 откат не делается — прерванным такой запуск быть не мог; START_WAIT_S=0 — один запрос
  if ver_gt v1.1.0 "$TAG" || { [ "$PREV_LINK" = ".venvs/$TAG" ] && START_WAIT_S=0 up "$TAG"; }; then
    echo "версия $TAG уже установлена"
    exit 0
  fi
  finish_interrupted
fi
ver_gt "$CUR" "$TAG" || refuse "$TAG новее установленной $CUR; обновление — scripts/update.sh"
ver_gt v1.1.0 "$TAG" && refuse "ниже v1.1.0 откат не делается: у $TAG нет журнала версий и проверки /api/version"
rc=0; last="$(hr_root release-last)" || rc=$?
[ "$rc" = 5 ] && refuse "в журнале версий нет удачного обновления — откатывать не на что"
[ "$rc" = 0 ] || refuse "не удалось прочитать журнал версий (база недоступна?)"
read -r FROM _ <<<"$last"
[ "$FROM" = - ] || ! ver_gt "$FROM" "$TAG" \
  || refuse "$TAG старше $FROM, с которой было последнее обновление: база проверена на совместимость только с ней"
for v in $(sed -n 's/^## \(v[0-9][0-9.]*\) — .*/\1/p' CHANGELOG.md 2>/dev/null); do
  { ver_gt "$v" "$TAG" && ! ver_gt "$v" "$CUR"; } || continue
  no="$(awk -v v="$v" '$0 ~ "^## " { on = ($2 == v); st = 0 } on && /^### / { st = ($0 == "### Откат"); next }
                       on && st && NF { print; exit }' CHANGELOG.md)"
  [[ "$no" =~ ^[[:space:]]*нет ]] && refuse "у $v в CHANGELOG.md «Откат: $no»"
done
echo "сейчас $CUR, возвращаем $TAG; база не меняется"

# 3. venv цели — до остановки: есть — только ссылка, нет — установка (нужна сеть)
say "3/7 зависимости"
VENV="$ROOT/server/.venvs/$TAG"
req_sum="$(git show "$TAG:server/requirements.txt" | sha256sum | cut -d ' ' -f 1)"
if [ -x "$VENV/bin/python" ] && [ "$(cat "$VENV/.ruware-requirements" 2>/dev/null)" = "$req_sum" ]; then
  echo "venv $TAG уже есть"
else
  rm -rf "$VENV"
  mkdir -p "$ROOT/server/.venvs"
  reqf="$(mktemp "${TMPDIR:-/tmp}/ruware-req.XXXXXX")"
  git show "$TAG:server/requirements.txt" >"$reqf"
  if command -v uv >/dev/null; then
    UV_HTTP_TIMEOUT=60 uv venv -q -p 3.12 "$VENV" 9>&- \
      && UV_HTTP_TIMEOUT=60 uv pip install -q --python "$VENV/bin/python" -r "$reqf" 9>&- \
      || { rm -rf "$VENV" "$reqf"; refuse "зависимости $TAG не установились (PyPI или сеть) — запустите ещё раз"; }
  else
    python3 -m venv "$VENV" 9>&- \
      && "$VENV/bin/pip" install -q --timeout 60 -r "$reqf" 9>&- \
      || { rm -rf "$VENV" "$reqf"; refuse "зависимости $TAG не установились (PyPI или сеть) — запустите ещё раз"; }
  fi
  rm -f "$reqf"
  echo "$req_sum" >"$VENV/.ruware-requirements"
fi

# 4. Фоновые задачи (сбор hh) — ждём до JOB_WAIT_MIN
say "4/7 фоновые задачи"
waited=0
while :; do
  rc=0; hr_root jobs-running || rc=$?
  case "$rc" in
    0) break ;;
    4) [ $((waited * JOB_POLL_S)) -ge $((JOB_WAIT_MIN * 60)) ] \
         && refuse "фоновая задача идёт дольше $JOB_WAIT_MIN мин — дождитесь её конца и запустите снова"
       sleep "$JOB_POLL_S"; waited=$((waited + 1)) ;;
    *) refuse "не удалось проверить фоновые задачи (база недоступна?)" ;;
  esac
done

# 5. Журнал версий — кодом текущей версии, до переключения
say "5/7 журнал версий"
hr_root release-record --kind rollback --from "$CUR" --to "$TAG" --commit "$COMMIT" \
  --note "откат по решению людей, база не менялась" >/dev/null \
  || refuse "не удалось записать откат в журнал версий (база недоступна?)"

# провал 6–7: текущая версия возвращается, в журнал — возврат на неё, чтобы «по журналу версий» снова было $CUR
switch_failed() {
  trap - ERR
  trap '' HUP INT TERM  # возврат доводится до конца и при повторном сигнале
  set +e
  echo "Шаг не прошёл: $1. Возвращаю $CUR." >&2
  eval "$STOP_CMD" 9>&-
  git checkout -q --detach "$CUR"
  ln -sfn "$PREV_LINK" server/.venv
  start_back
  local ok=1 note="откат на $TAG не прошёл: $1"
  up "$CUR" || { ok=0; note="$note; после возврата портал не ответил"; }
  hr_root release-record --kind auto_rollback --from "$TAG" --to "$CUR" --commit "$(git rev-parse HEAD)" \
    --note "$note" $([ "$ok" = 1 ] || echo --failed) >/dev/null \
    || echo "предупреждение: запись не попала в журнал версий" >&2
  echo
  if [ "$ok" = 1 ]; then
    echo "Откат не прошёл, работает $CUR, база не менялась"
  else
    echo "Откат не прошёл, код $CUR возвращён, но портал НЕ ОТВЕЧАЕТ — проверьте службы"
  fi
  exit 1
}

# 6. Остановка, код и venv цели — без migrate
say "6/7 остановка и код $TAG"
trap 'switch_failed "ошибка в строке $LINENO"' ERR
trap 'switch_failed "прервано"' INT TERM
trap 'on_hup; switch_failed "обрыв SSH"' HUP
eval "$STOP_CMD" 9>&- || switch_failed "STOP_CMD завершился с ошибкой"
git checkout -q --detach "$TAG" || switch_failed "не переключился код"
ln -sfn ".venvs/$TAG" server/.venv

# 7. Запуск и проверка
say "7/7 запуск и проверка"
eval "$START_CMD" 9>&- || switch_failed "START_CMD завершился с ошибкой"
up "$TAG" || switch_failed "портал не ответил версией $TAG за $START_WAIT_S с"
trap - ERR INT TERM
trap on_hup HUP  # портал уже на $TAG: итог дописывается и после обрыва SSH

keep="$(basename "$PREV_LINK")"
for d in server/.venvs/*/; do
  d="${d%/}"
  case "$(basename "$d")" in "$TAG"|"$keep") ;; *) rm -rf "$d" ;; esac
done
ahead="$(hr_root version 2>/dev/null | sed -n 's/^база новее кода (так бывает после отката): //p' || true)"
echo
echo "Откат прошёл: $CUR → $TAG (коммит $COMMIT), база не менялась"
[ -n "$ahead" ] && echo "миграции новее кода остаются в базе: $ahead"
echo "вернуться на $CUR — scripts/update.sh $CUR"
