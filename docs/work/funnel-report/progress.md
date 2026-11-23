# Журнал: funnel-report

<!-- Здесь факты, а не планы. Статусы задач — только в plan.md. -->

## Handoff

<!-- Перезаписывается в конце каждой сессии и итерации. Здесь всё, что нужно следующей сессии с чистым контекстом. -->

- Этап: 2 (Гейт 1: человек утвердил план 2026-11-23; plan-reviewer — один проход)
- Последнее сделанное: T1 — миграция `server/migrations/012_funnel_stages.sql` (таблицы, справочники, право, настройка)
- Следующий шаг: T2 — `ops.funnel_carry()` дописать в ту же `012` (она ещё не выпущена, в замке `RELEASED.sha256` её нет)
- Важно знать:
  - Человек 2026-11-23: работать автономно циклом до выпуска v1.3.0; в конце — письмо на ehnvad84@gmail.com: протокол, инструкция обновления рабочего сервера, ссылки, описание.
  - Тесты: `cd server && DATABASE_URL=postgresql://postgres@127.0.0.1:55432/ruware_hr_main_test qa-test .venv/bin/pytest -q -o addopts="" -p no:warnings` (`-o addopts=""` — иначе `-q` дважды и строки итога нет); JS — `node --test tests/js/*.test.mjs`; линтер `.venv/bin/ruff check .`. База — `~/.local/pg16`, порт 55432, `pg_ctl` вручную, после работы остановить (на 23.11 уже была запущена до итерации T1 — не останавливалась).
  - `test_release.py::test_s10_changelog_matches_version` красный до T15 (012 не названа в CHANGELOG) — ожидаемо, как в semantic-search.
  - Совместимость: `DATABASE_URL=… bash server/scripts/compat_check.sh v1.2.0` (из корня репозитория).
  - Коды в `012`: строки `ops.funnel_row` — `selected contact screening interview meeting security offer first_day`; причины `ops.fail_reason` — failed: `skills test_task security`; refused: `conditions offer_declined(only_row offer) no_contact no_show no_first_day`; `other` — `outcome IS NULL` (у обоих исходов), `needs_comment`. Стандартные этапы `ops.stage` названы как строки отчёта (`is_standard`), доп.: «Тестовое задание», «Оплачиваемое тестовое» → `interview`, «Собеседование с руководителем» → `meeting`.
  - Тяжёлые команды — только с префиксом `qa-test`.
  - `.env*` агентам закрыт; новые переменные — только в инструкции админу.
  - Excel-образец ОП в репозиторий не класть (ФИО).

## Журнал

<!-- Только добавлять, сверху вниз: дата · задача · что сделано · коммит · отклонения от плана или спеки. -->

- 2026-11-23 · T1 · миграция `server/migrations/012_funnel_stages.sql`: `ops.funnel_row` (8 строк), `ops.fail_reason` (9 причин S7), `ops.stage` (8 стандартных + 3 доп.), `ops.stage_set` (`UNIQUE (vacancy_id, version)`, `EXCLUDE` — один `proposed`), `ops.stage_set_item` (`PK (set_id, pos)`, `UNIQUE (set_id, slot)`), `ops.candidate_stage` (два `EXCLUDE USING btree` среди неотменённых: слот пары один раз, одна открытая отметка; `CHECK` не `passed` ⇒ `reason`), `ops.vacancy_pause` (`EXCLUDE` — одна открытая, `to_date >= from_date`); право `head_recruiter:stage.approve` с записью `agent_change` kind `permission`; `stage_fail_other_min` = 100 через `ops.change_default('012', …)`. Тесты в `test_migrate.py` (8 новых по S1, S7, S14 и ограничениям), счётчик записей о правах 3→4. · коммит — этот (`T1: …`) · отклонения/допущения: (1) `ops.fail_reason.outcome` допускает пусто — так «Другое» одной строкой с `code` PK принадлежит обоим исходам (спека: «`other` — у обоих исходов»); (2) `only_row` только у `offer_declined`, как в спеке; «Не вышел в первый день» к строке не привязан — подстановку на последнем этапе делает интерфейс (макет C); (3) в `compat_skip.txt` новых строк нет — нужные исключения уже есть, добавлен комментарий о проверке v1.2.0; (4) `test_s10_changelog_matches_version` красный до T15.
  Доказательство: `pytest` (полный) — `1 failed, 697 passed in 115.63s` (упал только test_s10_changelog_matches_version, ожидаемо до T15); `ruff check .` — `All checks passed!`; `compat_check.sh v1.2.0` — `687 passed, 3 deselected … Совместимость: тесты v1.2.0 зелёные на миграциях HEAD`; линтер DDL `test_s6_unreleased_migrations_only_add` — зелёный.
