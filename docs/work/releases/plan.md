# План: releases

<!-- Чеклист для итераций и Ralph loop (.workflow/WORKFLOW.md, Этап 2).
Состав задач меняется только перепланированием (Этап 1). На Этапе 2 меняются только отметки:
[ ] не начата · [x] готова · [!] блокер, причина — в progress.md
Формат строки задачи не менять: его читает .workflow/ralph.sh. -->

Спека: docs/work/releases/feature-spec.md · Ветка: feature/releases

Тесты — `qa-test pytest` на базе `ruware_hr_test`; репетиция T11 — только маленькие базы `*_test` и каталоги в
scratchpad, после удаляются (решение человека 10.11: диск пробного VPS бережём).

- [x] T1 — миграция `009_release.sql`: `ops.release`, `ops.release_notice`, `ops.change_default` · сценарий: S8 · проверка: auto · исполнитель: main
  Готово, когда: тесты S8 — не трогали → новое значение + `agent_change`; поменял человек → `release_notice`; ключа нет → вставка; повторный `migrate` ничего не меняет
- [x] T2 — `VERSION`, `CHANGELOG.md` (v1.0.0 и v1.1.0 с «Ручными шагами»: таймер копии, deploy key), `hr/version.py`, `db.pending()` без изменения поведения `migrate()` · сценарий: S1, S10 · проверка: auto · исполнитель: main
  Готово, когда: `test_release.py::test_version_status` (code/db/pending/ahead, `code` без VERSION — по тегу или `unknown`) и `test_migrate.py` зелёные
- [ ] T3 — команды `hr.cli`: `version`, `migrate --pending`, `jobs-running` (порог `load_stuck_hours`), `release-record`, `env-missing`, `job-record` · сценарий: S1, S3, S9 · проверка: auto · исполнитель: main
  Готово, когда: тесты кодов выхода (3 — есть неприменённые, 4 — идёт задача, зависшая — предупреждение) и `env-missing` печатает только имена — зелёные
- [ ] T4 — `GET /api/version` без входа (роутер в `hr/main.py`) и строка версии в «Как работает портал» · сценарий: S1 · проверка: auto · исполнитель: main
  Готово, когда: тест — без cookie ответ ровно `{version, date}`; node-тест `guide.js` показывает строку версии
- [ ] T5 — замок `migrations/RELEASED.sha256` (001–008), уникальность номеров, линтер DDL (`-- contract:`, тела `$$…$$` пропускаются), CHANGELOG ↔ VERSION · сценарий: S6, S7, S10 · проверка: auto · исполнитель: main
  Готово, когда: тесты падают на изменённой выпущенной миграции, дубле номера, `DROP` без пометки, VERSION без раздела CHANGELOG — и проходят на текущем дереве с `009`
- [ ] T6 — `scripts/db_copy.sh [--check-space]` (`.new` → `pg_restore --list` → `mv`, `RUWARE_LOCK_HELD`), `install_autostart.sh` не трогаем · сценарий: S12 · проверка: manual · исполнитель: main
  Готово, когда: в репетиции T10 копия заменяется; при нехватке места и ошибке `pg_dump` прежняя копия цела, выход ≠ 0
- [ ] T7 — `scripts/update.sh` (повторный exec из тега, код цели в `.releases/<тег>`, flock после exec, место до STOP, проверка симлинка `.venv`, очистка `.venvs/` до двух), `server/update.conf.example` (systemd user, Docker, образец недельного таймера копии), `.gitignore` (`.venv`, `.venvs/`, `.releases/`, `update.conf`) · сценарий: S2, S3, S4, S9 · проверка: manual · исполнитель: main
  Готово, когда: `bash -n` чисто; репетиция T10 проходит S2 (v1.0.0 → v1.1.0 командой первого запуска), все отказы S3 без остановки портала, возврат S4 на v1.0.0 с проверкой `/`; в `.venvs/` две версии
- [ ] T8 — `scripts/rollback.sh` без `migrate`; отказ ниже v1.1.0, старше `from_version`, при «Откат: нет»; `release-record` до checkout · сценарий: S5 · проверка: manual · исполнитель: main
  Готово, когда: в репетиции T10 откат v1.1.1 → v1.1.0 проходит и v1.1.0 отвечает на базе новой; три отказа срабатывают
- [ ] T9 — `scripts/compat_check.sh <тег>`, `tests/compat_skip.txt`, `scripts/release.sh vX.Y.Z [--dry-run]` (pytest через `qa-test`) · сценарий: S6, S10 · проверка: auto · исполнитель: main
  Готово, когда: `compat_check.sh v1.0.0` (локальный тег на `2bf9c68`, не пушим) зелёный на миграциях HEAD, база `*_compat_test` и worktree удалены; `release.sh v1.1.0 --dry-run` проходит без коммита; без раздела CHANGELOG — отказ
- [ ] T10 — репетиция на пробном VPS в scratchpad: bare-репозиторий как origin с тегами v1.0.0, v1.1.0 и v1.1.1 (только локально); свой `.env` с базой `ruware_hr_upd_test` и `update.conf` (порт 18000, временный uvicorn, `PG_DUMP` по `*_test`); установка v1.0.0 с настоящим `.venv` → переход S11 → обновления → порча старта (S4) → `rollback.sh` → отказ `db_copy.sh` (S12) · сценарий: S2, S3, S4, S5, S11, S12 · проверка: manual · исполнитель: main
  Готово, когда: журнал шагов и итогов в progress.md, все сценарии отмечены; `server/.env` пробного VPS не читался, службы пробного VPS не тронуты; тестовые базы, каталоги и venv удалены
- [ ] T11 — `ОБНОВЛЕНИЕ_портала.md` для инженеров (первый раз S11 с переносом `.venv`, команда первого запуска, обычное обновление, откат, недельная копия, восстановление вручную), `УСТАНОВКА_на_сервер.md` (git clone вместо ZIP), AD-8 в `docs/rules/architecture.md` и ссылка в `conventions.md`, строка о версии в справке и мануалах → docx · сценарий: S11 · проверка: manual · исполнитель: main
  Готово, когда: инструкция повторяет ровно шаги T10; `build_docx.py` собирает мануалы

Этап 5 (после мержа, не задачи Этапа 2): `release.sh v1.1.0` на `main` (VERSION, замок с `009`); теги `v1.0.0` на `2bf9c68` и `v1.1.0`; `git push origin v1.0.0 v1.1.0`; проверка `git ls-remote --tags origin`.
