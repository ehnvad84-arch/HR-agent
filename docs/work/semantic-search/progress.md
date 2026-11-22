# Журнал: semantic-search

<!-- Здесь факты, а не планы. Статусы задач — только в plan.md. -->

## Handoff

<!-- Перезаписывается в конце каждой сессии и итерации. Здесь всё, что нужно следующей сессии с чистым контекстом. -->

- Этап: 2 — разработка
- Последнее сделанное: T1 — миграция `010_semantic_search.sql` (2026-11-22)
- Следующий шаг: первая задача `[ ]` в plan.md (T2)
- Важно знать:
  - Тесты: `cd server && DATABASE_URL=postgresql://postgres@127.0.0.1:55432/ruware_hr_main_test qa-test .venv/bin/pytest -q -p no:warnings`; линтер `.venv/bin/ruff check .`. База — `~/.local/pg16` (данные `~/.local/pg16-data`), порт 55432, поднимается `pg_ctl` вручную (службы ruware на VPS выключены и не включаются). Сводка «N passed» видна с `-o addopts=""`.
  - Ожидаемо красный до T17: `tests/test_release.py::test_s10_changelog_matches_version` — новая миграция 010 должна быть названа в разделе CHANGELOG, совпадающем с VERSION; раздел v1.2.0 и VERSION — задача T17 (release.sh). Остальные тесты зелёные.
  - compat_check: `DATABASE_URL=… bash server/scripts/compat_check.sh v1.1.0` — зелёный; исключения v1.1.0 — в `server/tests/compat_skip.txt`.
  - Тяжёлые команды — только с префиксом `qa-test` (песочница VPS).
  - Модель, hh и bge-m3 — только заглушки в тестах; резюме на сервере разработки не собираем.
  - `setup_hh_oauth.py` в корне — чужой неотслеживаемый файл: не трогать, не коммитить.

## Журнал

<!-- Только добавлять, сверху вниз: дата · задача · что сделано · коммит · отклонения от плана или спеки. -->

- 2026-11-22 · T1 · миграция `server/migrations/010_semantic_search.sql`: `role_synonym` (public, PK role_code+keyword), `ops.profile_embedding` (vector(1024)), `ops.daily_quota` (PK day+key), `ops.service_health`; право `profile.synonyms` рекрутеру и руководителю ОП с записью `agent_change` kind `permission`; 10 настроек через `ops.change_default('010', …)`. Тесты в `test_migrate.py`: таблицы и настройки, права и журнал, атомарный upsert `daily_quota`. · коммит — этот (`T1: …`) · отклонения: (1) `role_synonym` в public, как в спеке — в public теперь 47 таблиц, не 46 из приёмки ТЗ п.2: `test_public_has_46…` переименован в `…_47…`, счётчик записей о правах 2→3; пункт 2 приёмки ТЗ надо поправить в T17 или ретро; (2) `updated_at` у `role_synonym` и колонки `ops.profile_embedding` получили `DEFAULT now()`/`NOT NULL` — таблицы новые, сужения нет; (3) в `compat_skip.txt` четыре исключения для v1.1.0: два теста знают число таблиц и записей о правах, `test_s10_changelog_matches_version` знает список миграций, `test_s10_release_without_changelog_section_refused` запускает release.sh во worktree без `.venv` — от базы не зависит; (4) `test_s10_changelog_matches_version` красный до T17 (раздел CHANGELOG v1.2.0 и VERSION).
  Доказательство: `pytest` — `1 failed, 508 passed` (упал только test_s10_changelog_matches_version, ожидаемо); `ruff check .` — `All checks passed!`; `compat_check.sh v1.1.0` — `502 passed, 4 deselected … Совместимость: тесты v1.1.0 зелёные на миграциях HEAD`.
