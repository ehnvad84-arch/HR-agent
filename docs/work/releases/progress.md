# Журнал: releases

<!-- Здесь факты, а не планы. Статусы задач — только в plan.md. -->

## Handoff

<!-- Перезаписывается в конце каждой сессии и итерации. Здесь всё, что нужно следующей сессии с чистым контекстом. -->

- Этап: 2 — реализация (Ralph); Гейт 1 пройден — человек запустил Этап 2
- Последнее сделанное: T1 — `009_release.sql` (`ops.release`, `ops.release_notice`, `ops.change_default`), тесты S8 в `tests/test_release.py`
- Следующий шаг: T2 — VERSION, CHANGELOG.md, `hr/version.py`, `db.pending()`
- Важно знать: пробный VPS — только маленькие базы `*_test`; `server/.env` не читать; тег v1.0.0 — `2bf9c68` (проверено по reflog); мерж и теги — Этап 5; `pytest` по `pyproject` идёт с `-q` без строки итога — для итога `-o addopts=""`; `test_collect_hh.py::test_search_limit_is_not_captcha[daily_resume_search_limit_exceeded]` изредка падает в общем прогоне, отдельно и при повторе зелёный (к releases не относится)

## Журнал

<!-- Только добавлять, сверху вниз: дата · задача · что сделано · коммит · отклонения от плана или спеки. -->

- 2026-11-10 · T1 · `server/migrations/009_release.sql`: `ops.release`, `ops.release_notice`, функция `ops.change_default(миграция, ключ, старое, новое, пометка)`; `server/tests/test_release.py` — 7 тестов S8. Коммит `T1: миграция 009_release…`.
  Проверено: `qa-test .venv/bin/pytest -o addopts="" tests/test_release.py -v` → `7 passed in 2.46s` (не трогали → новое значение + `agent_change` с `applied_by NULL`; поменял человек в портале → `release_notice` «настройка alert_score_new оставлена как есть»; поменяли SQL-ом → то же; ключа нет → вставка + `agent_change`; повторный вызов → ничего; `db.migrate() == []`).
  Полный прогон: первый — `1 failed, 399 passed` (`test_collect_hh.py::test_search_limit_is_not_captcha[daily_resume_search_limit_exceeded]`; отдельно — `2 passed`, весь файл на дереве без T1 — `24 passed`), повтор — `400 passed in 69.54s`. `ruff check .` → `All checks passed!`.
  Допущения: (1) ключа нет в `settings` — кроме вставки пишется `agent_change` (`before NULL`): conventions.md требует запись каждого изменения поведения; (2) значение уже равно новому умолчанию — функция ничего не пишет и возвращает `false`, без ложной заметки «оставлена как есть»; (3) `p_note` необязательный (`DEFAULT NULL`), в `agent_change.note` — «выпуск <миграция>[: <пометка>]».
