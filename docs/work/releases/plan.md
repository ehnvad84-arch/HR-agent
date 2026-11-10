# План: releases

<!-- Чеклист для итераций и Ralph loop (.workflow/WORKFLOW.md, Этап 2).
Состав задач меняется только перепланированием (Этап 1). На Этапе 2 меняются только отметки:
[ ] не начата · [x] готова · [!] блокер, причина — в progress.md
Формат строки задачи не менять: его читает .workflow/ralph.sh. -->

Спека: docs/work/releases/feature-spec.md · Ветка: feature/releases

Тесты — `qa-test pytest` на базе `ruware_hr_test`; репетиция T11 — только маленькие базы `*_test` и каталоги в
scratchpad, после удаляются (решение человека 10.11: диск пробного VPS бережём).

- [ ] T1 — `VERSION`, `CHANGELOG.md` (разделы v1.0.0 и v1.1.0), `hr/version.py`, `db.pending()` без изменения поведения `migrate()` · сценарий: S1, S10 · проверка: auto · исполнитель: main
  Готово, когда: `test_release.py::test_version_status` (code/db/pending/ahead) и `test_migrate.py` зелёные
- [ ] T2 — миграция `009_release.sql`: `ops.release`, `ops.release_notice`, `ops.change_default` · сценарий: S8 · проверка: auto · исполнитель: main
  Готово, когда: тесты двух путей S8 (не трогали → новое значение + `agent_change`; поменял человек → `release_notice`) и повторный `migrate` без изменений зелёные
- [ ] T3 — команды `hr.cli`: `version`, `migrate --pending`, `jobs-running`, `release-record`, `env-missing`, `job-record` · сценарий: S1, S3, S9 · проверка: auto · исполнитель: main
  Готово, когда: тесты кодов выхода (3 — есть неприменённые, 4 — идёт задача, зависшая > 6 ч — предупреждение) и `env-missing` печатает только имена — зелёные
- [ ] T4 — `GET /api/version` без входа и строка версии в «Как работает портал» · сценарий: S1 · проверка: auto · исполнитель: main
  Готово, когда: тест — ответ ровно `{version, date}` без cookie; node-тест `guide.js` показывает строку версии
- [ ] T5 — замок `migrations/RELEASED.sha256` (001–008), уникальность номеров, линтер DDL с `-- contract:`, проверка CHANGELOG ↔ VERSION · сценарий: S6, S7, S10 · проверка: auto · исполнитель: main
  Готово, когда: тесты падают на изменённой выпущенной миграции, дубле номера, `DROP` без пометки, VERSION без раздела CHANGELOG — и проходят на текущем дереве
- [ ] T6 — `scripts/db_copy.sh` (`.new` → `pg_restore --list` → `mv`) и таймер `ruware-db-copy` (вс 03:00) в `install_autostart.sh` · сценарий: S12 · проверка: manual · исполнитель: main
  Готово, когда: в репетиции T11 копия заменяется; при нехватке места и при ошибке `pg_dump` прежняя копия цела, выход ≠ 0
- [ ] T7 — `scripts/update.sh`, `server/update.conf.example` (systemd user и Docker), `.gitignore` (`.venv`, `update.conf`, `.venvs/`) · сценарий: S2, S3, S4, S9 · проверка: manual · исполнитель: main
  Готово, когда: `bash -n` чисто; репетиция T11 проходит S2, отказы S3 и автоматический возврат S4
- [ ] T8 — `scripts/rollback.sh` без `migrate`, с отказом при «Откат: нет» и цели старше `from_version` · сценарий: S5 · проверка: manual · исполнитель: main
  Готово, когда: в репетиции T11 откат v1.1.0 → v1.0.0 проходит, прежняя версия отвечает на базе новой; оба отказа срабатывают
- [ ] T9 — `scripts/compat_check.sh <тег>` и `tests/compat_skip.txt` · сценарий: S6 · проверка: auto · исполнитель: main
  Готово, когда: `compat_check.sh v1.0.0` (тег локально на `2bf9c68`) зелёный на миграциях HEAD; база `*_compat_test` и worktree удалены
- [ ] T10 — `scripts/release.sh vX.Y.Z`: ruff, pytest, pip-audit, compat_check, VERSION, замок, коммит · сценарий: S10 · проверка: auto · исполнитель: main
  Готово, когда: прогон на ветке для v1.1.0 проходит и дописывает 009 в замок; без раздела CHANGELOG — отказ
- [ ] T11 — репетиция на пробном VPS: локальный bare-репозиторий как origin с тегами v1.0.0 и v1.1.0-rc, установка v1.0.0 из архива → переход S11 → `update.sh` → порча старта (S4) → `rollback.sh` → `db_copy.sh` с отказом (S12) · сценарий: S2, S3, S4, S5, S11, S12 · проверка: manual · исполнитель: main
  Готово, когда: журнал шагов и итогов в progress.md, все сценарии отмечены; тестовые базы, каталоги и venv удалены
- [ ] T12 — `ОБНОВЛЕНИЕ_портала.md` для инженеров (первый раз S11, обычное обновление, откат, восстановление из копии вручную), правка `УСТАНОВКА_на_сервер.md` (git clone вместо ZIP), правила миграций в `docs/rules/conventions.md`, строка о версии в мануалах → docx · сценарий: S11 · проверка: manual · исполнитель: main
  Готово, когда: инструкция повторяет ровно шаги T11; `build_docx.py` собирает мануалы
- [ ] T13 — после мержа в `main`: тег `v1.0.0` на `2bf9c68`, тег `v1.1.0` на коммит мержа, `git push origin v1.0.0 v1.1.0` · сценарий: S2 · проверка: manual · исполнитель: main
  Готово, когда: `git ls-remote --tags origin` показывает оба тега; VERSION на `v1.1.0` = `v1.1.0`
