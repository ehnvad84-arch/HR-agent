# Журнал: releases

<!-- Здесь факты, а не планы. Статусы задач — только в plan.md. -->

## Handoff

<!-- Перезаписывается в конце каждой сессии и итерации. Здесь всё, что нужно следующей сессии с чистым контекстом. -->

- Этап: 2 — реализация (Ralph); Гейт 1 пройден — человек запустил Этап 2
- Последнее сделанное: T2 — `VERSION` (v1.1.0), `CHANGELOG.md` (v1.1.0, v1.0.0), `hr/version.py` (`code_version`, `release_date`, `status`), в `db.py` — `migration_files()`, `applied()`, `pending()`
- Следующий шаг: T3 — команды `hr.cli`: `version`, `migrate --pending`, `jobs-running`, `release-record`, `env-missing`, `job-record`
- Важно знать: пробный VPS — только маленькие базы `*_test`; `server/.env` не читать; тег v1.0.0 — `2bf9c68` (проверено по reflog); мерж и теги — Этап 5; `pytest` по `pyproject` идёт с `-q` без строки итога — для итога `-o addopts=""`; `test_collect_hh.py::test_search_limit_is_not_captcha[daily_resume_search_limit_exceeded]` изредка падает в общем прогоне, отдельно и при повторе зелёный (к releases не относится)

## Журнал

<!-- Только добавлять, сверху вниз: дата · задача · что сделано · коммит · отклонения от плана или спеки. -->

- 2026-11-10 · T1 · `server/migrations/009_release.sql`: `ops.release`, `ops.release_notice`, функция `ops.change_default(миграция, ключ, старое, новое, пометка)`; `server/tests/test_release.py` — 7 тестов S8. Коммит `T1: миграция 009_release…`.
  Проверено: `qa-test .venv/bin/pytest -o addopts="" tests/test_release.py -v` → `7 passed in 2.46s` (не трогали → новое значение + `agent_change` с `applied_by NULL`; поменял человек в портале → `release_notice` «настройка alert_score_new оставлена как есть»; поменяли SQL-ом → то же; ключа нет → вставка + `agent_change`; повторный вызов → ничего; `db.migrate() == []`).
  Полный прогон: первый — `1 failed, 399 passed` (`test_collect_hh.py::test_search_limit_is_not_captcha[daily_resume_search_limit_exceeded]`; отдельно — `2 passed`, весь файл на дереве без T1 — `24 passed`), повтор — `400 passed in 69.54s`. `ruff check .` → `All checks passed!`.
  Допущения: (1) ключа нет в `settings` — кроме вставки пишется `agent_change` (`before NULL`): conventions.md требует запись каждого изменения поведения; (2) значение уже равно новому умолчанию — функция ничего не пишет и возвращает `false`, без ложной заметки «оставлена как есть»; (3) `p_note` необязательный (`DEFAULT NULL`), в `agent_change.note` — «выпуск <миграция>[: <пометка>]».
- 2026-11-10 · T2 · `VERSION` = `v1.1.0`; `CHANGELOG.md` — разделы v1.1.0 (ручные шаги: первый переход и deploy key, `update.conf`, недельный таймер копии) и v1.0.0, у каждого 5 подразделов `###`; `server/hr/version.py`; в `server/hr/db.py` выделены `migration_files()`, `applied(conn)`, `pending(conn)`, `migrate()` применяет ровно `pending` (локальная переменная переименована в `done`, чтобы не затенять `applied`). Тесты `test_version_status`, `test_version_code_without_version_file`. Коммит `T2: …`.
  Проверено: `qa-test .venv/bin/pytest -o addopts="" tests/test_release.py tests/test_migrate.py -q` → `19 passed in 2.58s`; полный прогон → `402 passed, 1 warning in 68.06s`; `ruff check .` → `All checks passed!`.
  Допущения: (1) `VERSION` = `v1.1.0` уже сейчас, чтобы T4/T5 проверяли настоящую пару VERSION ↔ CHANGELOG; `release.sh` на Этапе 5 его подтвердит; (2) запасной путь `git describe --tags --exact-match` — в `code_version()`, а не только в `status()`: тогда и `/api/version` (T4) на установке без VERSION покажет тег; принимается только вид `vX.Y.Z`; (3) `applied` в `status()` — число применённых миграций («сколько применено», S1); (4) `pending()` на пустой базе начинается с `schema_recruiting.sql` — ровно то, что применит `migrate()`; (5) `db` — последняя запись `ops.release` с `ok`, порядок по `coalesce(finished_at, started_at)`; (6) дата v1.0.0 — 2026-10-30, дата коммита `2bf9c68`; с него в `server/migrations` добавлена только `009` (`git log --name-status 2bf9c68..HEAD -- server/migrations server/.env.example` → `A server/migrations/009_release.sql`).
