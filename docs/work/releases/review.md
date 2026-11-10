# Ревью: releases

Ревьюер: code-reviewer (Claude Opus 5.5) · Контекст: свежий · Дифф: main...feature/releases (14 коммитов, 37 файлов, +3032/−27) · Дата: 2026-11-10

**Вердикт:** approve — blocker нет ни здесь, ни в security-check.md. Важные находки В1–В3 рекомендую закрыть до
Этапа 5. В1 касается скрипта самой v1.1.0: при первом переходе у заказчика выполняется `update.sh` из тега v1.1.0.

Дифф прочитан целиком, кроме двоичных `.docx`: их пересборку подтверждает журнал T11, правку руками не проверял.
`ОБНОВЛЕНИЕ_портала.md` прочитан выборочно: разделы 1–3 и восстановление.

## 1. Дифф: что изменилось и почему

- `server/migrations/009_release.sql`: `ops.release`, `ops.release_notice`, `ops.change_default` (T1, S8). Только добавляет, линтер проходит.
- `VERSION`, `CHANGELOG.md`, `hr/version.py`, в `hr/db.py` выделены `migration_files/applied/pending` (T2, S1, S10). Поведение `migrate()` то же: набор и порядок миграций не изменились, повторный запуск ничего не меняет.
- `hr/release.py`, `hr/cli.py`: команды `version`, `migrate --pending`, `jobs-running`, `release-record`, `release-last`, `env-missing`, `job-record`, `db-size` (T3, T6, T8).
- `hr/api/version.py` и роутер в `main.py`, строка версии в `guide.js`, `guide-view.js`, `schema.js` (T4, S1).
- `migrations/RELEASED.sha256`, замок, линтер DDL и проверка CHANGELOG в `tests/test_release.py` (T5, S6, S7, S10).
- `scripts/db_copy.sh` (T6), `update.sh` (T7), `rollback.sh` (T8), `compat_check.sh` и `release.sh` (T9), `update.conf.example`, `.gitignore`.
- Тесты `test_update.py`, `test_rollback.py`, `test_db_copy.py`; `ОБНОВЛЕНИЕ_портала.md`, `УСТАНОВКА_на_сервер.md`, AD-8, `conventions.md`, мануалы и docx (T11).
- Изменения вне плана, все записаны в progress.md как допущения:
  - новые команды `hr.cli db-size` и `release-last` (в спеке их нет; нужны, потому что по AD-1 bash не ходит в базу);
  - `rollback.sh` ставит venv цели до STOP, а в спеке порядок «STOP → checkout → venv»: отклонение в пользу S3;
  - `rollback.sh` после STOP не проверяет `jobs-running` повторно;
  - фикс T10: `env-missing` берёт `.env.example` из корня репозитория;
  - в `diff -r` (S11) добавлены `-x .pytest_cache -x .ruff_cache`.

  Все отклонения обоснованы. Возражений нет.

## 2. Соответствие спеке

| Сценарий | Реализован | Тест | Замечание |
|---|---|---|---|
| S1 | да | `test_release.py::test_version_status`, `::test_cli_version_and_pending_exit_codes`, `::test_api_version_without_login`, `::test_version_code_without_version_file`, `tests/js/guide.test.mjs` | |
| S2 | да | `test_update.py::test_s2_first_update_from_v100`, `::test_s2_several_versions_at_once`, `::test_s2_no_new_migrations_no_copy`, `::test_s2_same_version_is_ok` | Сохранность резюме, `.env`, `data` и настроек проверена только репетицией T10 (вручную), автотеста нет |
| S3 | да | `test_update.py::test_s3_*` (7 тестов), `::test_s3_update_conf_writable_by_others` | В2: ложная причина «мало места», если у цели новая зависимость |
| S4 | да | `test_update.py::test_s4_auto_rollback[2]` | Прерывание (INT, HUP) на шагах 6–7 не покрыто, см. В1 |
| S5 | да | `test_rollback.py` (11 тестов), `test_release.py::test_cli_release_last` | Отказ при недоступной сети, когда venv цели нужно ставить, не покрыт |
| S6 | частично | `test_release.py::test_s6_*`, `::test_s6_compat_check_unknown_tag` | Линтер с пробелами (В3); сам `compat_check.sh` проверен только вручную (T9) |
| S7 | да | `test_release.py::test_s7_released_migrations_locked`, `::test_s7_changed_deleted_or_duplicate_migration_fails` | Замок можно обойти, поправив и сам `RELEASED.sha256` (М10) |
| S8 | да | `test_release.py::test_s8_*` (6), строка в итоге — `test_update.py::test_s2_first_update_from_v100` | |
| S9 | частично | `test_release.py::test_cli_env_missing_prints_names_only`, `::test_env_missing_default_example_is_repo_root` | Нет теста «портал стартует без новой переменной»; `SESSION_KEY` нет в `.env.example` (старый долг) |
| S10 | да | `test_release.py::test_s10_*`, `::test_s10_release_without_changelog_section_refused` | |
| S11 | да (инструкция) | автотеста нет, только репетиция T10 | Ручной сценарий, так и задумано в плане |
| S12 | да | `test_db_copy.py` (8 тестов), `test_update.py::test_s12_copy_failed_portal_started_again` | Остаток `.new` после kill не покрыт (М4) |

### Непокрытые сценарии и случаи для test-writer

1. update.sh и rollback.sh: прерывание (HUP или TERM) после `git checkout` и повторный запуск. Сейчас это даёт «уже установлена», выход 0, а портал стоит (В1).
2. update.sh: у цели новая зависимость, которую импортирует цепочка `hr.cli`, и есть новые миграции (В2).
3. Линтер DDL: `ADD CHECK`, `ADD UNIQUE`, `ADD PRIMARY KEY`, `ADD FOREIGN KEY` без слова CONSTRAINT; `ALTER a TYPE` без COLUMN; `DO $$ … $$`; `UPDATE ONLY settings` и `UPDATE "settings"` (В3).
4. update.sh: SIGINT во время `migrate` на шаге 6 приводит к возврату прежней версии.
5. rollback.sh: venv цели нужно ставить, PyPI недоступен. Ожидается отказ без STOP.
6. db_copy.sh: после kill остался `ruware.dump.new`; `BACKUP_DIR` уже существует с правами 755.
7. S2 автотестом: `settings` и `app_user` до и после обновления одинаковы.
8. S9: портал стартует без новой переменной `.env`, интеграция выключена.
9. release.sh без локальных тегов: compat пропускается молча (М8).

## 3. Безопасность

Вердикт security-check.md: pass. Подробно — `docs/work/releases/security-check.md`.

## 4. Тесты и edge cases

- Полный прогон: `cd server && qa-test .venv/bin/pytest -q -o addopts="" -p no:warnings` → `488 passed in 100.45s`.
- `ruff check .` → `All checks passed!`.
- `node --test tests/js/*.mjs` → `tests 353, pass 353, fail 0`.
- `qa-test .venv/bin/pip-audit -r requirements.txt` → `No known vulnerabilities found`.
- `shellcheck` не установлен, не запускался. Скрипты проверены чтением; линтер DDL — прогоном `ddl_problems` на образцах (вывод в В3).
- Не покрыто тестами: список выше. Плюс конкурентность: таймер копии и update.sh берут один замок `flock -n`, этот путь покрыт тестами `lock_busy`.

## Находки

Уровни: blocker / важное / мелочь. Происхождение у всех находок «новое», если не сказано иное.

| # | Уровень | Где | Проблема и риск | Минимальный фикс |
|---|---|---|---|---|
| В1 | важное | `server/scripts/update.sh:91`, `:157-158`, `:207-208`; `server/scripts/rollback.sh:74`, `:173-174` | **Прерывание после checkout не восстанавливается.** Ловушки стоят только на INT, TERM и ERR, на HUP её нет. Инженер обновляет по SSH. Если связь рвётся (SIGHUP) или процесс убит на шаге 6–7, а это и долгий `migrate` на базе около 10 ГБ, скрипт умирает без `switch_failed`. Портал и таймеры остановлены, HEAD уже на теге, `.venv` указывает на новый venv, миграции могли пройти не все. Повторный `update.sh <тег>` видит CUR = TAG и печатает «версия уже установлена» с выходом 0: портал не запускается, неприменённые миграции не применяются. Это противоречит спеке: «инженер перезапускает команду, шаги идемпотентны». У rollback.sh то же самое. **Как воспроизвести:** в фикстуре `inst` заглушка `migrate` делает `sleep 30`; послать `kill -HUP` процессу update.sh; запустить `update.sh` с тем же тегом ещё раз. Результат: выход 0, `START_CMD` не вызывался, `hr.cli migrate --pending` возвращает 3. | 1) `trap … HUP` вместе с INT и TERM на шагах 5–7 в обоих скриптах. 2) В ветке CUR = TAG проверять `hr.cli migrate --pending` и `up "$TAG"`. Если миграции не применены или портал молчит, довести шаги 6–7 (migrate, START, проверка) или отказать с понятным текстом. 3) В `ОБНОВЛЕНИЕ_портала.md` §3 и §2.7: запускать в `tmux`/`screen` или через `systemd-run --user --wait`. |
| В2 | важное | `server/scripts/update.sh:108-109` | **Проверка места запускает код цели в venv текущей версии.** Если в цели появилась зависимость, которую тянет импорт `hr.cli` (cli → jobs → …), `db-size` падает с ImportError. `db_copy.sh` выходит с кодом 1, update.sh отказывает с ложной причиной «освободите место в BACKUP_DIR». Обновиться на такую версию нельзя. **Как воспроизвести:** в теге v1.2.0 добавить в `hr/jobs.py` `import aiogram` и пакет в `requirements.txt`, добавить миграцию `010_x.sql`; в текущем venv aiogram нет. `update.sh v1.2.0` → выход 1, «освободите место». Для перехода на v1.1.0 не срабатывает: `requirements.txt` не менялся. Для следующих версий срабатывает, и как раз ожидается бот с aiogram. | Перенести `--check-space` после шага 3 и звать с `RUWARE_PY="$TPY"`. Оба шага идут до STOP, S3 не страдает. В `db_copy.sh` различать «мало места» (1) и «не удалось запустить» (2), а update.sh пусть пишет причину по коду выхода. |
| В3 | важное | `server/tests/test_release.py:259-269`, `:303`, `:330` | **Линтер DDL (S6) пропускает сужения**, которые спека прямо требует ловить («`ADD CONSTRAINT` (CHECK/UNIQUE/FK)»). Проверено вызовом `ddl_problems`, результат `[]` у каждого образца: `ALTER TABLE x ADD CHECK (a > 0)`, `ADD UNIQUE (a)`, `ADD PRIMARY KEY (a)`, `ADD FOREIGN KEY …`, `ALTER TABLE x ALTER a TYPE bigint` (без COLUMN), `UPDATE ONLY settings …`, `update "settings" …`, а также всё внутри `DO $$ … $$`. DO-блок выполняется при миграции сразу, это не тело функции. **Как воспроизвести:** миграция `ALTER TABLE decision_log ADD CHECK (length(comment) >= 300);` без `-- contract:` проходит `test_s6_unreleased_migrations_only_add`. После отката v(N-1) на записи с комментарием 200–299 знаков получает CheckViolation. `compat_check.sh` поймает это, только если тесты N-1 пишут такие данные, а это не гарантировано. | Шаблоны: `\bADD\s+(CONSTRAINT|CHECK|UNIQUE|PRIMARY\s+KEY|FOREIGN\s+KEY|EXCLUDE)\b`; `\bALTER\s+(COLUMN\s+)?"?\w+"?\s+(SET\s+DATA\s+)?TYPE\b`; `\bUPDATE\s+(ONLY\s+)?("?public"?\.)?"?settings"?\b`. Вырезать только тела `CREATE [OR REPLACE] FUNCTION/PROCEDURE … AS $$…$$`, а `DO $$…$$` проверять или запрещать без `contract:`. Добавить эти образцы в `test_s6_narrowing_without_contract_fails`. |
| М1 | мелочь | `server/scripts/db_copy.sh:39-44` | `.env` подгружается (`set -a; .`) в окружение всего скрипта. Все секреты (ключи hh, `SECRETS_KEY`, LLM) уходят в окружение `eval "$PG_DUMP"` и `$PG_RESTORE` (docker exec и т.п.), хотя нужны только вызовам `hr.cli`. Кроме того, `.env` исполняется как bash, а в спеке сказано «Скрипты `.env` не выполняют»: значение с `$`, `` ` `` или пробелом без кавычек исказится или выполнится. Так же `.env` грузит и `install_autostart.sh` (старый подход). Нужна проверка, как `.env` загружается у заказчика. | Грузить `.env` только в подоболочке `hr_cli()`, как это делает `hr_in` в update.sh. Уточнить в спеке формулировку «не выполняют». |
| М2 | мелочь | `server/scripts/db_copy.sh:30-32` | Недельный таймер вызывает `db_copy.sh` напрямую, и права `update.conf` при этом не проверяются, в отличие от `update.sh:43`. В файле `eval`-команда `PG_DUMP`. | Та же проверка `& 022`, выход 2. |
| М3 | мелочь | `server/scripts/db_copy.sh:51` | `umask 077` + `mkdir -p` не меняет права у уже существующего `BACKUP_DIR`: например, `/var/backups/ruware` с 755 так и останется. Спека требует каталог 700. | `chmod 700 "$BACKUP_DIR"` или отказ, если права шире. |
| М4 | мелочь | `server/scripts/db_copy.sh:81` | Ловушки на выход нет. Kill, HUP или остановка юнита таймера во время дампа оставляют `ruware.dump.new` размером до ~10 ГБ. Следующая проверка места может ложно отказать «мало места». | `trap 'rm -f "$NEW" "$DUMP.info.new"' EXIT` (снимать после `mv`) или удалять старый `.new` до проверки места. |
| М5 | мелочь | `server/scripts/update.sh:173`, `:175`; `server/scripts/rollback.sh:142` | `curl` без `--max-time`: если портал завис на ответе, `up()` не ограничен `START_WAIT_S`. | `curl --max-time 5 …` |
| М6 | мелочь | `server/scripts/update.sh:159-161` | Если задача стартовала между шагом 4 и STOP и `STOP_CMD` её убил, у неё остаётся `finished_at IS NULL`. Итог — отказ, и следующие запуски тоже отказывают до `load_stuck_hours` (3 ч). | Описать в инструкции (§3, таблица отказов) или после STOP считать незавершённые задачи убитыми и закрывать их через `hr.cli`. |
| М7 | мелочь | `ОБНОВЛЕНИЕ_портала.md:142-143`, `server/scripts/update.sh:5-6` | Команда первого запуска пишет в предсказуемый `/tmp/ruware-update.sh` в общем `/tmp`. На многопользовательском сервере чужой файл можно подменить между записью и `bash`, при `fs.protected_regular=0`. | `f=$(mktemp) && git show … > "$f" && bash "$f" v1.1.0` или файл в `~`. |
| М8 | мелочь | `server/scripts/release.sh:60-63`, `:88-92` | Если локальных тегов нет, проверка совместимости пропускается со строкой «не нужна». План Этапа 5 ставит `release.sh v1.1.0` раньше тега v1.0.0, значит compat в настоящем выпуске не пройдёт. В T9 её прогоняли вручную, поэтому риск низкий. | `git fetch --tags` в начале. Если VERSION есть, а прошлого тега нет — отказ. На Этапе 5 сначала поставить тег v1.0.0 на `2bf9c68`. |
| М9 | мелочь | `server/scripts/release.sh:71` | `restore` делает `git checkout -- VERSION LOCK` из индекса. Если `git commit` упал после `git add` (хук, нет user.email), в индексе уже новые версии: файлы не вернутся и останутся в индексе. | `git checkout -q HEAD -- VERSION "$LOCK"` (вместе с индексом). |
| М10 | мелочь | `server/tests/test_release.py:356-359` | `RELEASED.sha256` сам не защищён. Если поправить выпущенную `009` и её сумму в замке, S7 пройдёт. Жёстко зашиты только номера 001–008. | Замок текущего дерева обязан содержать замок прошлого тега построчно (`git show <prev>:server/migrations/RELEASED.sha256`); проверять в `release.sh` и в тесте, когда тег есть. |
| М11 | мелочь | `server/hr/api/version.py:12-14`, `server/hr/version.py:23-24` | Без файла VERSION каждый запрос к `/api/version` без входа запускает `git describe` подпроцессом. | `functools.lru_cache` на `code_version()` и `release_date()`: версия не меняется без рестарта. |
| М12 | мелочь | `CHANGELOG.md:52` | «по `server/.env.example`», а образец лежит в корне (фикс T10). | Поправить путь. |
| М13 | мелочь | `server/scripts/compat_check.sh:70-71` | Копируются файлы рабочего дерева, а не HEAD, как сказано в спеке. Если запускать отдельно на грязном дереве, проверяются незакоммиченные миграции. В `release.sh` дерево чистое, там это неважно. | `git archive HEAD server/migrations schema_recruiting.sql \| tar -x -C …` или проверка чистоты дерева. |

### Старый долг (Гейт 3 не блокирует)

| # | Уровень | Где | Проблема | Фикс |
|---|---|---|---|---|
| Д1 | мелочь | `.env.example` | Нет `SESSION_KEY`, поэтому `env-missing` (S9) его не назовёт. Отмечено в Handoff как вопрос к человеку. | Добавить имя без значения. |
| Д2 | мелочь | `server/scripts/install_autostart.sh:15` | `.env` подгружается через bash `.`, то есть исполняется: та же семантика, что в М1. | Единый разбор `KEY=VALUE` без исполнения, если понадобится. |

## Повторное ревью (круг 2)

Ревьюер: code-reviewer (Claude Opus 5.5) · Контекст: свежий · Дифф: `02892ce..HEAD` (коммиты 93f642e, f86aafe; 6 файлов,
+224/−51), прочитан целиком · Дата: 2026-11-10

**Вердикт:** approve: новых blocker нет. В2 и В3 закрыты. В1 закрыт частично: на шагах 6–7 обрыв SSH ловится,
повторный запуск доводит прерванную установку. Но при настоящем обрыве SSH ловушка шага 5 не запускает портал
(Н1), а запись журнала, которую делает доведение, снимает защиту отката (Н2). Н1 и Н2 рекомендую закрыть до Этапа 5.
Новая версия скрипта работает уже при первом переходе на v1.1.0.

### Закрыты ли В1–В3

| # | Итог | Почему |
|---|---|---|
| В1 | частично | HUP добавлен в ловушки шагов 5–7 обоих скриптов. Ветка «версия уже стоит» доводит шаги 6–7, если ссылка venv не на цели, есть неприменённые миграции или портал не отвечает. Зациклиться она не может: `finish_interrupted` всегда заканчивается `exit 0` или `exit 1`. Остаются Н1 и Н2. |
| В2 | закрыт | `--check-space` идёт после шага 3 с `RUWARE_PY="$TPY"`, как и копия на шаге 5. Тест `test_s3_new_dependency_space_checked_with_target_venv` проверяет это по сути: `db-size` в venv v1.0.0 падает, обновление проходит. Коды выхода `db_copy.sh` (1 и 2) не разделены, но текст отказа теперь отсылает к причине строкой выше, и таблица в инструкции дополнена. |
| В3 | закрыт | Все образцы из В3 ловятся, они добавлены в `test_s6_narrowing_without_contract_fails`. Ложных срабатываний на допустимых миграциях нет: `ADD COLUMN … UNIQUE`, `ADD COLUMN … CHECK`, `ALTER TYPE … ADD VALUE`, `CREATE TABLE` с CHECK, UNIQUE, PK и FK, `ALTER COLUMN kind SET DEFAULT`, триггер `BEFORE UPDATE ON settings`, `UPDATE settings_audit`, тело `CREATE FUNCTION … $$` дают `[]`. Проверено вызовом `ddl_problems`. Исключение — Н5. |

### Находки круга 2

Все находки — «новое», то есть внесены коммитами 93f642e и f86aafe.

| # | Уровень | Где | Сценарий сбоя | Минимальный фикс |
|---|---|---|---|---|
| Н1 | важное | `server/scripts/update.sh:207`, `:261-264`, `:97`, `:102`; `ОБНОВЛЕНИЕ_портала.md:208-209` | **При настоящем обрыве SSH ловушка шага 5 не запускает портал. Итог шага 9 не пишется в журнал.** При обрыве SSH терминал (pty) закрывается, и после этого любая запись в stdout или stderr возвращает EIO. В `abort_stopped` первым идёт `echo … >&2`, а `set -e` всё ещё включён. Функция вызвана из ловушки, `errtrace` не задан, поэтому скрипт просто завершается с кодом 1: `start_back` не вызывается, портал и таймеры остаются остановлены. Шаг 5 включает копию базы около 10 ГБ, это минуты, и обрыв в это время вполне вероятен. То же после `trap '' HUP` на `:261`: `say "9/9 итог"` падает, и `release-record` не выполняется. Поэтому комментарий «итог и журнал дописываются и после обрыва SSH» неверен. Повторный запуск видит, что портал отвечает и миграций нет, пишет «уже установлена» и записи в журнале так и не делает. Дальше `rollback.sh` сверяется с устаревшим `release-last`. В `switch_failed` есть `set +e`, поэтому шаги 6–7 работают. Инструкция обещает, что обрыв на шагах 5–7 скрипт ловит сам. Для шага 5 это неверно. **Воспроизведение** (на модели с теми же строками: `set -euo pipefail`, те же `abort_stopped` и `trap`, `sleep 3` вместо копии). Запустить через `pty.fork()` и через 0,5 с закрыть master. Итог: выход 1, `start_back` не вызван. Контроль: с `set +e` в начале `abort_stopped` функция `start_back` вызывается. Отдельно: `printf` после `trap '' HUP` при закрытом pty под `set -e` даёт выход 1, следующая строка не выполняется. Тест `test_s3_sighup_during_migrate_returns_previous` этого не ловит: вывод идёт в pipe, а не в pty. | В `abort_stopped`, а также после `trap '' HUP` на `:261` и в `finish_interrupted` поставить `set +e` или перенаправить вывод в журнал: `exec >>"$BACKUP_DIR/update.log" 2>&1` либо `… \|\| true` на выводе. Надёжнее сначала писать в журнал версий, потом печатать. Тест — тот же сценарий в `pty` (`script -qc` или `pty.fork`) с `PG_DUMP_CMD="sleep 30"` и закрытием терминала, проверить, что `START_CMD` вызван. |
| Н2 | важное | `server/scripts/update.sh:107-108`; влияет на `server/scripts/rollback.sh:116-118` | **Доведённая установка пишется в журнал без `--from` и снимает защиту отката S5.** `release-last` печатает `- vX`, и в `rollback.sh` срабатывает `[ "$FROM" = - ]`: отказ «старше версии, с которой было последнее обновление» пропускается. Сценарий: v1.1.0 → v1.2.0 прошло; v1.2.0 → v1.3.0 убито (kill -9, перезагрузка) после миграций; повторный запуск доводит установку и пишет `update NULL → v1.3.0`. Затем `rollback.sh v1.1.0` проходит проверку, хотя совместимость базы v1.3.0 проверялась только с v1.2.0. Та же запись появляется и без всякого прерывания, см. Н3. | Брать `--from` из `basename "$PREV_LINK"`, если там другая версия, а иначе — `to_version` последней удачной записи из `release-last`. Если последняя запись уже указывает на `$TAG`, не писать новую. В тест `test_s2_interrupted_update_finished_by_rerun` добавить проверку `--from v1.0.0`. |
| Н3 | мелочь | `server/scripts/update.sh:139`, `server/scripts/rollback.sh:105` | **Один неудачный запрос перезапускает исправный портал.** С `START_WAIT_S=0` это ровно одна попытка `curl` без `--max-time`. Если портал в этот момент на секунду не ответил, `finish_interrupted` делает STOP, `migrate` и START, не проверив `jobs-running`. Идущий сбор hh убивается, у него остаётся `finished_at IS NULL`, и следующие обновления отказывают до `load_stuck_hours` (М6). В журнал попадает лишняя запись из Н2. Остановить исправный портал навсегда эта ветка не может: START вызывается, и при отказе выводится `stuck`. | Дать проверке несколько попыток (`START_WAIT_S=6`). В `finish_interrupted` перед STOP, если портал отвечает, проверять `jobs-running`. |
| Н4 | мелочь | `server/scripts/update.sh:136-138` | `migrate --pending` в ветке «версия уже стоит» вызывается через `server/.venv`, то есть через venv прежней версии, если процесс убит между `git checkout` (`:248`) и `ln` (`:249`). Если у новой версии новая зависимость (как в В2), будет ImportError и отказ «миграции не проверить (база недоступна?)» с началом «Обновление не начато, портал работает». На деле портал остановлен, а повторный запуск ничего не доводит. Окно — между двумя соседними командами и только при SIGKILL или перезагрузке. | Звать `server/.venvs/$TAG/bin/python`, если он есть. Текст отказа в этой ветке — без «портал работает». |
| Н5 | мелочь | `server/tests/test_release.py:264` | Ложное срабатывание: `COLUMN` стал необязательным, поэтому `ALTER TABLE x ALTER COLUMN type SET DEFAULT 'a'` даёт «ALTER COLUMN … TYPE» (`\S+` захватывает слово `COLUMN`). В текущей схеме колонки `type` нет, есть `kind`, так что сейчас это не мешает. | `\bALTER\s+(COLUMN\s+)?(?!COLUMN\s)\S+\s+(SET\s+DATA\s+)?TYPE\b`, добавить образец в тест на допустимые миграции. |
| Н6 | мелочь | `server/tests/test_release.py:306`, `:331-332` | Остаются пропуски в DO-блоке. Проверка `ADD COLUMN … NOT NULL без DEFAULT` смотрит только операторы, которые начинаются с `ALTER TABLE`, а внутри DO оператор начинается с `BEGIN`. Поэтому `DO $$ BEGIN ALTER TABLE x ADD COLUMN a int NOT NULL; END $$;` даёт `[]`. `EXECUTE 'ALTER TABLE x DROP COLUMN y'` тоже даёт `[]`, потому что строки вырезаны. Последнюю линию держит `compat_check.sh`. | Внутри DO искать `ALTER TABLE` через `re.search`, а не `re.match`. `EXECUTE` в DO без `contract:` запрещать. |
| Н7 | мелочь | `ОБНОВЛЕНИЕ_портала.md:142-143`, `:167`, `:224` | `tmux` советуют в §3 и §4, но не в «Первом разе» (§2). А именно первый переход v1.0.0 → v1.1.0 и был поводом для В1. Не сказано, что `tmux` нужно поставить (`apt install tmux`), и что `tmux new -s update` при уже открытой сессии даёт ошибку `duplicate session`. В абзаце «Если обновление прервалось» для отката сообщение будет «Откат … не доведён», а не «Установка … не доведена». | Добавить `tmux` в §2. Писать `tmux new -A -s update`. Одной строкой — как поставить. |

### Тесты

- `qa-test .venv/bin/pytest -o addopts="" -q -p no:warnings` → `504 passed in 106.28s`. Отдельно `test_update.py`, `test_rollback.py`, `test_release.py`, `test_db_copy.py` → `111 passed`.
- `ruff check .` → `All checks passed!`
- Регрессионные тесты В2 и В3 достаточны. Для В1 проверены HUP на `migrate` (через pipe) и доведение повторным запуском. Не покрыто, список для test-writer:
  1. Обрыв терминала (pty, а не pipe) на шаге 5 во время копии: `START_CMD` вызван (Н1).
  2. Обрыв терминала после `:261`: запись `release-record --kind update --from` есть (Н1).
  3. HUP на шаге 6 `rollback.sh`: возврат текущей версии.
  4. Доведение пишет `--from` прежней версии (Н2).
  5. `finish_interrupted` при `MIGRATE_FAIL`: выход 1, текст «не доведена» (ветка `stuck`).
  6. Ссылка venv на цели, миграций нет, портал не отвечает: доведение без `migrate`-изменений; и наоборот, портал отвечает со второй попытки (Н3).
  7. Линтер: `ALTER COLUMN type SET DEFAULT` не ловится (Н5); `NOT NULL` без `DEFAULT` внутри DO ловится (Н6).

### Безопасность

Изменения касаются только сигналов, порядка шагов и текста инструкции. Новых секретов, прав и входов нет, `.env`
по-прежнему грузится только в подоболочку `hr_in`. `security-check.md` не менялся. Snyk не запускался: по
`docs/rules/security.md` п.5 он заменён на `pip-audit`, а зависимости в этом диффе не менялись. `.claude/`, хуки и
скиллы не менялись, поэтому `agentshield` не нужен.
