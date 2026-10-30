# Ревью: manager-cabinet

Ревьюер: независимый ревьюер (Claude, субагент) · Контекст: свежий · Дифф: `feature/workplace...feature/manager-cabinet` (HEAD e797376) · Дата: 2026-10-30

**Вердикт:** request-changes

Blocker нет. Есть пять major. Две из них касаются безопасности (контакты в досье руководителя, токен Телеграма в журнале), три — недоделанных сценариев спеки (S1 «ежедневно», S16, S7 «заметки»). Approve по правилу допустим и при major, но M2 и M5 — прямые меры из модели угроз спеки, и в текущем виде они не выполняются. Поэтому прошу исправить major до мержа.

## 1. Дифф: что изменилось и почему

121 файл, +6531/−282. Сервер прочитан целиком: `hr/`, `hr/api/`, миграции 006–008, тесты Python. Экраны JS просмотрены выборочно: поиск `innerHTML` и `href` по всему диффу, досье `person.js`, `manager-vacancies.js`, `nav-model.js`, `tweaks-model.js`, `learn.js`. Снимки экранов из ручных задач T9–T13, T16, T18 я не проверял.

- T1 — миграции `006_user_role_no_access.sql` (enum отдельным файлом), `007_manager_cabinet.sql` (6 таблиц в `ops`, индексы, настройки, права с записью в `agent_change`), `008_load_check.sql` (настройки S20).
- T2 — `hr/scope.py` (`for_user`, `person_ok`), `auth.require_scoped`, страж `routes_unscoped`, `scope` в `/api/me`.
- T3 — скоуп в `api/{today,base,person,funnel,vacancies,profiles,learning,my}.py`; `views.for_manager` убирает `url`, `social`, `decisions`; `audit_log` на просмотр досье у всех без `base.all`; `/api/schema` теперь требует `base.all`.
- T4 — `hr/access.py`, `api/access.py`, `POST /api/login/code`, отказ роли `no_access` в `auth.require`.
- T5 — `hr/staff_1c.py`, `/api/access/sync`, `/upload`; `org_1c.latest_file` пропускает `employees*`.
- T6 — `hr/handoff.py`, `api/handoff.py`, `views.manager_today`, блок передач в «Сегодня» HR.
- T7 — `vacancies.create` с обязательными ответственным и заказчиком, `default_manager`; загрузка hh по праву `collect.run`.
- T8 — `hr/tweaks.py`, `api/tweaks.py`, `keywords_minus` в `scoring.apply_criteria`, `ops.user_match`, `sort=my`, `learning.feed` по автору, S16 через `build_digest`/`apply_digest`.
- T17 — `hr/load_check.py`, хук `jobs.run → load_check.after_run`, `api/notify.py`, таймер в `install_autostart.sh`.
- T19 — `learning.week_text`/`feedback_week`, `hr/telegram.py`, таймер на пятницу, `check_env.py`.
- T9–T13, T16, T18 — экраны и node-тесты; T14 — мануалы, `docs/1c/выгрузка_сотрудников.md`, AD-7, уточнение AD-3, PRD.
- Изменения вне плана:
  - `УСТАНОВКА_на_сервер.md` (коммит be0b86c «установка портала на свой сервер») — документ, в план не входит.
  - Новые эндпойнты `GET /api/staff` и `GET /api/vacancy/defaults` (`api/vacancies.py:57-69`). Их нет в таблице контрактов, но они нужны форме вакансии S12; права `vacancy.edit`.
  - Предложения личных поправок делает регулярное выражение (`tweaks.suggest`), а не модель, как описано в спеке (раздел «Личное обучение», A1). Для безопасности так даже лучше, но в `progress.md` это отклонение не записано.

## 2. Соответствие спеке

| Сценарий | Реализован | Тест | Замечание |
|---|---|---|---|
| S1 | частично | `test_staff_1c.py::test_s1_roles_by_rule`, `::test_s1_override_kept_and_fired_revoked`, `::test_m6_too_many_missing_rejected`, `::test_api_upload_and_bad_file` | «ежедневно» не сделано: нет задачи в `TASKS` и таймера в `install_autostart.sh` (находка 3) |
| S2 | да | `test_access.py::test_s2_manual_user_and_role` | |
| S3 | да | `test_access.py::test_s3_code_login_flow`, `::test_m3_expired_and_lock` | для логина с живым кодом блокировка даёт 429, для неизвестного логина — всегда 401 (находка 9) |
| S4 | да | `test_access.py::test_s4_no_access` | |
| S5 | частично | `test_scope_points.py::test_manager_sees_only_own`, `::test_dossier_without_links` | в досье руководителя видны контакты из свободного текста резюме (находка 1) |
| S6 | да | — | нет теста на `views.manager_today`: сигналы только по кодам скоупа, счётчик подходящих, отсутствие действий |
| S7 | частично | `test_scope_points.py` (404 на чужую воронку) | сервер не отдаёт заметки отдела персонала в воронке (`funnel.funnel`), хотя экран ждёт `p.note` (находка 5); ответственный HR в списке есть |
| S8 | да | `test_scope_points.py::test_manager_sees_only_own` (403 / 200), `::test_hr_sees_all_and_positions_tree` | |
| S9 | да | `test_handoff.py::test_s9_send_and_s11_take`, `::test_out_of_scope_404_and_hr_cannot_send` | неактивная кнопка без вакансий — только в node-тестах |
| S10 | частично | `test_handoff.py::test_s9_send_and_s11_take`, `::test_s11_reject_reason_masked_for_manager` | «отказ с причиной» по `candidate.reject_reason` не отдаётся (`handoff.py:80`, находка 7) |
| S11 | да | `test_handoff.py::test_s9_send_and_s11_take`, `::test_already_candidate_only_linked`, `::test_s11_reject_reason_masked_for_manager` | |
| S12 | да | `test_vacancies.py::test_s12_responsible_and_customer` | |
| S13 | да | `test_vacancies.py::test_s13_collect_rights` | |
| S14 | да | `test_tweaks.py::test_s14_own_chat_and_agent_suggestion` | |
| S15 | да | `test_tweaks.py::test_s15_my_score_only_for_author` | |
| S16 | частично | `test_tweaks.py::test_s16_shared_goes_to_digest_then_pending_version` | тест вставляет `role_code` в базу напрямую; через API `role_code` всегда NULL, и тогда `apply_digest` версию портрета не создаёт (находка 4) |
| S17 | да | `test_scope.py::test_director_hr_and_no_unit`, node `nav.test.mjs` | |
| S18 | да | `test_staff_1c.py::test_s1_override_kept_and_fired_revoked` | нет теста «сменили подразделение → видимость пересчитана» |
| S19 | да | `test_scope_points.py` (`/api/schema` 403), node `guide.test.mjs` | соответствие текста правам из `role_permission` проверено только node-тестом |
| S20 | да | `test_load_check.py` (4 теста) | |
| S21 | частично | `test_feedback_week.py` (3 теста) | при отсутствии каналов сводка уходит только в журнал задачи, в «Обучении агента» её нет; если Телеграм упал, письмо уже не отправляется (находка 8) |

**Непокрытые сценарии и случаи — для `test-writer`:**
1. S5/M2 — досье руководителя: в `observations[].about` и `experience[].description` нет телефона и почты (сейчас тест упадёт, см. находку 1).
2. S6 — `GET /api/today` у руководителя: сигналы только по кодам скоупа, в `vacancies` только вакансии скоупа со счётчиком, ключей `actions`, `stalled`, `meetings` нет.
3. S7 — воронка вакансии руководителя содержит заметки HR без контактов (после реализации).
4. S10 — после «отказа» по воронке руководитель видит причину отказа кандидату.
5. S16 — сквозной путь через API: три пользователя добавляют одинаковую поправку через `POST /api/tweaks`, затем сводка, решение и `apply_digest` дают версию «ждёт утверждения».
6. S18 — смена `org_unit_id` у руководителя: следующий запрос возвращает другой скоуп, и прежний человек отдаёт 404.
7. S21 — без каналов сводка видна руководителю отдела персонала в «Обучении агента»; Телеграм падает, а письмо всё равно уходит.
8. M1 — `GET /api/search` у руководителя отфильтрован скоупом; `POST /api/person/{id}/explain` на чужого человека или чужой профиль отдаёт 404; `GET /api/my/positions` у директора.
9. M3 — `POST /api/login/code` для пользователя с ролью `no_access` (код выдан до смены роли).

## 3. Безопасность

Вердикт security-check.md: **fail** (две major без blocker: M2 «контакты в досье руководителя» и утечка токена Телеграма в журнал).

## 4. Тесты и edge cases

- Полный прогон: `cd server && DATABASE_URL=postgresql://postgres@127.0.0.1:55432/ruware_hr_review_test .venv/bin/pytest -q` → **387 passed, 0 failed** (junit: tests=387 errors=0 failures=0). Первый прогон дал волну `psycopg.OperationalError` (соединение с базой). Два следующих прогона подряд прошли чисто, `pg_isready` ок. Считаю это сбоем окружения, а не кода, но первопричину не выяснял.
- `node --test tests/js/*.test.mjs` → 351 pass, 0 fail.
- `ruff check .` → All checks passed.
- `pip-audit -r requirements.txt` → No known vulnerabilities found.
- Мои проверки (временный тест в scratchpad, в репозиторий не попал):
  - руководитель получает в `/api/person/{id}/dossier` почту и телефон из `raw.skills` и `experience[].description` — **подтверждено**;
  - для логина с живым кодом шестая попытка даёт 429, для неизвестного логина — 401 (видно по коду и `test_m3_expired_and_lock`).
- Не покрыто: перечень выше, плюс:
  - производительность `tweaks.rescore_person_all` при пересчёте сотен людей за прогон;
  - `telegram.chunks` на строке длиннее 4000 знаков при непустом буфере (код корректен, тест есть только на простой случай);
  - гонка двух «Взять» на одну передачу (`FOR UPDATE` есть, теста нет).

## Находки

### Новые (внесены этим диффом)

| # | Уровень | Где | Проблема и риск | Минимальный фикс |
|---|---|---|---|---|
| 1 | major | `server/hr/views.py:120` (`for_manager`), `server/hr/views.py:158` (`about`), `server/hr/views.py:140` (`description`) | M2 не выполнен. `for_manager` убирает `url`, `social`, `decisions`, но отдаёт руководителю свободный текст резюме: `observations[].about` (`raw->>'skills'`) и `experience[].description`. Кандидаты часто пишут там телефон и почту. Сценарий: на hh в «О себе» указан телефон → руководитель открывает досье → видит телефон и звонит в обход отдела персонала и `contact.open`. Подтверждено пробным тестом. Тест спеки M2 («ответ досье руководителю не содержит телефона, почты») не проверяет эти поля | в `for_manager` пропустить `about` и `experience[].description` через `learning.mask` (или убрать их); добавить тест с телефоном и почтой в `raw.skills` и `experience` |
| 2 | major | `server/hr/telegram.py:47`, `server/hr/jobs.py:135` | Токен бота в журнале. При ответе Телеграма 4xx/5xx (неверный чат, 429, отозванный токен) `raise_for_status()` бросает `HTTPStatusError`, в тексте которого полный URL `…/bot<TOKEN>/sendMessage`. `jobs.run` пишет его через `log.exception` с трассировкой в stderr, то есть в journald службы `ruware-feedback-week`. Нарушает security.md п.2 и п.3 | ловить `httpx.HTTPError` в `telegram.send` и бросать `JobError` без URL (только код ответа); URL не логировать |
| 3 | major | `server/hr/tasks.py:74` (нет задачи), `server/scripts/install_autostart.sh` (нет таймера) | S1 «ежедневно» и M5 не реализованы. Выгрузка сотрудников применяется только кнопкой «Обновить из 1С». Сценарий: руководителя уволили в 1С, файл лёг в обменный каталог, никто не нажал кнопку → он продолжает входить по паролю и видит ФИО и фото людей своего поддерева сколько угодно долго | задача `staff_sync` (`staff_1c.latest_file` + `apply`, отказ → `reject`) в `TASKS` и ежедневный таймер в `install_autostart.sh`; тест на задачу |
| 4 | major | `server/hr/api/tweaks.py:39`, `server/hr/learning.py:281-298`, `server/tests/test_tweaks.py:56` | S16 в реальном потоке не доходит до «версии портрета на утверждение». Через API поправка всегда получается с `role_code = NULL` («один набор на все должности»), и `apply_digest` при `profile_code is None` только пишет `agent_change` с пометкой «внесите вручную». Тест проходит, потому что ставит `role_code` прямо в базу, — путь, недостижимый через API. Тест создаёт ложную уверенность | решить с человеком, что делать с поправкой «на все должности» (например, версия по всем должностям из скоупа авторов или выбор должностей в окне решения); записать решение в спеку; тест — через `POST /api/tweaks` |
| 5 | major | `server/hr/funnel.py:60-82`, `server/web/js/screens/manager-vacancies.js:51` | S7: «воронка — этапы и заметки отдела персонала, без контактов». Сервер заметок не отдаёт: экран ждёт `p.note`, а его нет, поэтому маска `learning.mask` из таблицы точек чтения тоже не реализована. Отклонение не записано в `progress.md` | отдавать последнюю заметку HR по кандидату, через `learning.mask` для `not sc.all`; либо явно сузить S7 в спеке |
| 6 | minor | `.env.example` | Новые переменные `FEEDBACK_TG_TOKEN`, `FEEDBACK_TG_CHAT`, `TELEGRAM_API` не добавлены в `.env.example` (security.md п.2) | добавить имена без значений |
| 7 | minor | `server/hr/handoff.py:80-92` | S10: «вышел / отказ с причиной». `_LIST` не выбирает `candidate.reject_reason`, поэтому руководитель видит «отказ» без причины | добавить `c.reject_reason` и пропустить его через `learning.mask` в `mine` |
| 8 | minor | `server/hr/learning.py:354-361` | S21: если Телеграм упал, исключение роняет задачу, и письмо уже не уходит. При отсутствии каналов сводки нет в «Обучении агента» у руководителя отдела персонала (есть только запись в журнале задачи) | отправлять в каждый канал независимо; сохранять текст сводки (например, в `feedback_digest`) и показывать его на экране |
| 9 | minor | `server/hr/access.py:152-155` | Перебор логинов по ответу. Для логина с живым кодом пять ошибок дают 429, для неизвестного — всегда 401: можно узнать, у кого есть невыданный код. Кроме того, любой может каждые 15 минут блокировать код руководителя и мешать первому входу | для неизвестного логина отвечать так же (счётчик по логину) или отдавать 401 вместо 429; ограничение частоты по адресу — по желанию |
| 10 | minor | `server/hr/access.py:166` | Сброс пароля по коду (для `access.manage` и уже входивших) не гасит прежние сессии человека: если учётку угнали, сессия угонщика живёт до `session_max_days` | при успешном входе по коду — `UPDATE ops.web_session SET revoked_at` для прежних сессий |
| 11 | minor | `server/hr/learning.py:338-341` | В Телеграм и на почту уходят заголовки `digest_proposal` без маски (`title` содержит текст поправки «Сделать общей поправку «…»» и заголовки от модели). Замечания маскируются, предложения — нет | пропустить `title` через `mask` и `_FIO_LIKE`, как тело замечаний |
| 12 | minor | `server/hr/pipeline.py:202`, `server/hr/tweaks.py:157-160` | Производительность, нужна проверка на объёме. На каждого пересчитанного человека и каждого автора поправок `rescore_user` заново считает скоуп, `current_profiles`, `params` и делает запрос по каждой должности скоупа (у HR — все 83). Прогон сбора на 1000 человек при 10 авторах — сотни тысяч мелких запросов в транзакции сбора | считать скоуп и профили один раз на прогон или ставить пересчёт «моего балла» в фоновую `user_rescore` после сбора |
| 13 | minor | `server/hr/api/base.py:40-52` | Вакансия может попасть в скоуп через `hiring_manager_id`, хотя её должности нет в кодах скоупа. Тогда «Подходящие» показывают людей с ФИО, а досье и «Передать» по ним дают 404 (`person_ok` смотрит только коды). Утечки нет, но экран противоречит сам себе | в `person_ok` учитывать коды профилей вакансий скоупа или фильтровать подходящих по `person_ok` |
| 14 | minor | `server/hr/access.py:140-150` | Вход по коду не проверяет роль `no_access` (в отличие от `/api/login`): сессия создаётся, и уже потом все запросы получают 403. Вреда нет, но поведение расходится | отказ `no_access` в `login_code`, как в `login` |

### Старый долг (Гейт 3 не блокирует)

| # | Уровень | Где | Проблема | Фикс |
|---|---|---|---|---|
| D1 | minor | `schema_recruiting.sql:764`, `server/hr/api/reports.py:36,53` | У `director` есть `report.read`, а с ним `POST /api/report/weekly/build` и `/send`. Директор теперь реально входит в портал и может пересобрать и разослать недельный отчёт | отдельное право на сборку и отправку |
| D2 | minor | `server/hr/views.py:144-188` | `dossier` для HR тоже отдаёт свободный текст резюме с контактами в обход `contact.open` (до этой фичи) | решить вместе с находкой 1 |

## Исправлено после ревью (главная сессия, 2026-10-30)

- Major 1 (контакты в досье руководителя): `views.for_manager` — «О себе» и описания мест работы через `learning.strip_contacts`, версии резюме руководителю не отдаются · тест `test_m2_free_text_without_contacts_and_s18_unit_change`.
- Major 2 (токен Телеграма в журнале): `telegram.send` ловит `httpx.HTTPError` и отвечает `JobError` без адреса · тест `test_telegram_error_hides_token_and_mail_still_tried`.
- Major 3 (нет ежедневной выгрузки сотрудников): задача `staff_sync`, таймер `ruware-staff-sync` 06:10 МСК · тест `test_daily_sync_task`.
- Major 4 (S16 «на все должности»): общая поправка ложится версией «ждёт утверждения» в портреты должностей скоупа её авторов (`profile_codes`) · тест `test_s16_all_positions_tweak_reaches_profiles` через API.
- Major 5 (заметки в воронке, S7): `funnel.funnel` отдаёт `notes` (комментарии переводов), руководителю — без контактов.
- Minor: S10 — причина отказа кандидата видна руководителю (без контактов); S21 — Телеграм упал, письмо всё равно уходит; заголовки предложений в сводке под маской; вход по коду отказывает `no_access`; пароль по коду гасит прежние сессии.
- Оставлено: `.env.example` (`FEEDBACK_TG_*`, `TELEGRAM_API`, `EXCHANGE_1C_DIR`) — агенту файл закрыт, впишет человек; 429 против 401 на входе по коду (перебор всё равно закрыт блокировкой); нагрузка пересчёта «моего балла» при сборе — проверить на объёме; «Подходящие» и досье 404 у руководителя для людей из чужих поддеревьев с баллом по общей должности; `report.read` у директора — старый долг.
