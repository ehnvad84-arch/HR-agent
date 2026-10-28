# Предзапусковая проверка: исправления разбора ответов hh (core, лёгкий трек)

Дата: 2026-10-28 · Ревьюер: независимый агент · Предмет: незакоммиченный дифф `feature/core` (`git diff`), файлы `server/hr/hh.py`, `collect.py`, `jobs.py`, `cli.py`, тесты `test_collect_hh.py`, `test_mark_gone.py`, `test_rescore.py`.
Эталон форматов hh: официальная OpenAPI (локальная копия, недоверенные данные). Сверено: схемы ошибок `ErrorsCommonBadAuthorization*`, `ErrorsCommonCaptchaError`, `ErrorsCommonBadAuthorizationPaymentMethodError`, `ErrorsResumeTooManyRequests*`, ответы `GET /resumes`, `GET /resumes/{id}`, `/me` (`MeManagerProfile`), `method_access`, `payable_api_actions/active`, `limits/resume` (`EmployerManagersEmployerManagerLimits`).

## Вердикт

**Можно запускать первое живое подключение (`hh-auth` → `hh-selftest` → ручной `run collect_hh`).** Blocker нет. Необратимых последствий у запуска нет: всё, что может пойти не так, лечится повторным `hh-auth` или следующим прогоном. Перед регулярным запуском по расписанию стоит закрыть пункты M1–M3.

## Прогоны

- `pytest -q -p no:warnings` — 150 passed.
- `ruff check .` — All checks passed.
- Snyk — не запускался (не установлен).
- AgentShield — не нужен: хуки, MCP, `.claude/`, скиллы не менялись.

## Что проверено и в порядке

- **Разбор ошибок совпадает со спекой hh.** `token_expired` в `errors[].value` и `token-expired` в `oauth_error` ловятся оба. `view_limit_exceeded` (429 на `/resumes/{id}`) → `ViewLimitExceeded`. `daily_resume_search_limit_*` (429 на `/resumes`) → `JobLimit`, флага `blocked` нет, `retry` = False. Капча: у `ErrorsCommonCaptchaError` поле `value` всегда `captcha_required`, так что проверка `"captcha_required" in codes` срабатывает при любом `type` (`captcha_required`, `employer_resume_view`, …). `api_access_payment`/`action_must_be_payed` → `JobNotConfigured` про оплату. 429 без известного кода → `JobBlocked`. Небайтовый или не-JSON ответ обрабатывается без падения: `ValueError` перехватывается, дальше идёт общий `JobNotConfigured`.
- **Бесконечного цикла обновления токена нет.** В одном `Api.get` токен обновляется не больше одного раза (флаг `refreshed`). Если hh повторно ответит `token_expired`, `raise_for_hh` поднимет `JobNotConfigured(REAUTH_NEEDED)`. Цикл ограничен `range(len(RETRY_DELAYS)+1)` = 3 итерации. Если `/token` откажет, будет `JobNotConfigured` с `from None`. На отозванный токен (`token-revoked`, `bad_authorization`) запроса к `/token` нет, это покрыто тестом.
- **Секреты в сообщения не попадают.** Все тексты `JobError` — константы. Тело ответа hh и заголовок `Authorization` никуда не копируются. `HTTPStatusError` содержит только URL: путь, id резюме и параметры поиска, без токена. В выводе `hh-selftest` нет ФИО, почты и телефона менеджера, хотя `/me` их возвращает.
- **Частота запросов.** Все GET через `Api.get` и `selftest` идут через `RateLimiter.wait("hh")`. Тест проверяет, что пауза не меньше 0,99 с. Исключение — `/token`, см. M2.
- **Бюджет страниц.** Каждая попытка, включая повтор после обновления токена и повторы на 5xx, расходует страницу. Предел `crawl_max_pages` соблюдается.
- **`test_rescore`.** Переход на `%s::date - 1` от часов теста правильный: код (`pipeline.py:395`) сравнивает с параметром `now`, а не с `current_date`.

## Находки

### Blocker

Нет.

### Важное (major)

**M1. `hh-selftest` при обновлении токена может потерять новую пару токенов.** Происхождение: старый долг, новый код его усиливает (вместо одного запроса после обновления теперь пять).
`server/hr/cli.py:35-36` + `server/hr/hh.py:222`. `selftest` выполняется внутри одной транзакции `db.tx()`. Если срок токена в базе истёк, `access_token` обновляет его и сохраняет `store_tokens` в этой же транзакции. Потом любой следующий запрос может упасть: `/me` → «не менеджер», 404 на `method_access`/`limits`, сетевая ошибка, `JobNotConfigured` из `raise_for_hh`. Тогда транзакция откатывается. refresh_token у hh одноразовый (`invalid_grant: token has already been refreshed`), а в базе остаётся уже потраченный. Все задачи hh падают с «токен hh не обновился», и нужно снова проходить `hh-auth`.
На первом запуске сразу после `hh-auth` токен свежий, обновления не будет, поэтому это не blocker.
Фикс: получать токен в отдельной транзакции, которая коммитится до проверочных запросов. Например, `with db.tx() as c: token = access_token(c, …)`, а запросы выполнять уже после неё.

**M2. Запрос `/token` не проходит через `RateLimiter`: два запроса к hh меньше чем за секунду.** Происхождение: старый долг (путь 401 был и раньше), новый код делает его достижимым на реальном ответе hh (403 `token_expired`).
`server/hr/hh.py:109` (POST `/token`), `hh.py:190-192`, `hh.py:222` + `226`. Последовательность в `Api.get`: GET `/resumes` (t=0) → POST `/token` (t≈0,05) → GET через секунду после первого GET. В `selftest` и в `test_expired_token_refreshed_before_crawl` POST `/token` и первый GET идут подряд: у лимитера ещё нет предыдущей отметки. Это нарушает never.md («не чаще одного в секунду»), хотя по нагрузке разовое.
Фикс: передать лимитер в `access_token` (или в `_token_request`) и вызывать `limiter.wait("hh")` перед POST.

**M3. Резюме, принятые после `view_limit_exceeded` без полного текста, потом не дозапрашиваются.** Происхождение: старый долг (так же работает при исчерпании `hh_full_resume_per_run`), новый путь делает его частым, потому что суточный лимит просмотров кончается регулярно.
`server/hr/collect.py:49` + `56-58`. Элемент выдачи без навыков сохраняется в `observation.raw` с `updated_at` из выдачи. В следующем прогоне `updated` = False, поэтому `want` = False, и полное резюме не запрашивается, пока человек сам не обновит резюме. Подходящие кандидаты остаются с урезанными признаками (нет навыков и опыта), их балл занижен.
Фикс: считать «нужно полное», когда в сохранённом `raw` нет признака полного резюме (например, `skill_set` или `experience`), а не только по смене `updated_at`. Либо ставить такие резюме в очередь на дозапрос.

### Мелочи (minor)

- **m1. `mark_gone` при лимите просмотров обрывает весь цикл (`break`), а не только перепроверку hh.** Новое. `collect.py:151-153`. Устаревшие наблюдения других источников с `mode='api'` (в сиде есть `trudvsem`, сейчас выключен), стоящие в списке после первого hh, в этот прогон не помечаются пропавшими. Если `collect_hh` каждый день съедает лимит раньше `mark_gone`, они не помечаются никогда. Фикс: вместо `break` поставить `recheck = 0`, тогда оставшиеся hh пойдут в `skipped`, а остальные источники обработаются. Тот же `break` на `BudgetExhausted` — старый долг.
- **m2. `token_revoked` (через подчёркивание, `errors[].value`) и другие коды отказа токена не входят в `TOKEN_DEAD`.** Новое. `hh.py:130`. По спеке в `value` бывает `token_revoked`, `application_not_found`, `used_manager_account_forbidden`, `employer_on_additional_check_restricted`, а в `oauth_error` — `bad-auth-type`, `client-id-deleted`. Без `oauth_error` такой ответ даёт «нет доступа к базе резюме у этой учётной записи» вместо «пройдите hh-auth». Задача всё равно останавливается, но человек получает неверную подсказку. Фикс: дополнить множества кодами из спеки.
- **m3. Обновление на последней попытке.** Новое. `hh.py:190-198`. Если два раза подряд пришёл 5xx, а третий ответ 403 `token_expired`, токен обновится, цикл закончится, и `r.raise_for_status()` поднимет голый `HTTPStatusError(403)`. В `collect_hh` профиль получит пометку «hh не ответил», в `mark_gone` добавится `errors += 1`. Сбоя нет, сообщение неточное. Фикс: делать повтор после обновления вне счётчика попыток или после цикла вызывать `raise_for_hh(r)`.
- **m4. `selftest` не обновляет токен на 403 `token_expired`.** Новое. `hh.py:225-229`. Если hh считает токен истёкшим раньше нашего `expires_at`, проверка скажет «пройдите hh-auth», хотя хватило бы обновления. Фикс: использовать в `selftest` `Api` или ту же логику одного обновления.
- **m5. `selftest` берёт id менеджера только из `manager.id`.** Новое. `hh.py:232`. В спеке у `/me` есть ещё устаревшее `employer.manager_id`, а `manager` не обязателен. Если `manager` не придёт, проверка ложно скажет «токен выдан не менеджеру». Фикс: использовать `manager.id or employer.manager_id`.
- **m6. `selftest` не сверяет `employer.id` с `HH_EMPLOYER_ID`.** Новое. `hh.py:232-235`. Проверка пройдёт и под токеном чужого работодателя (`check_employer` это ловит только при авторизации). Фикс: сравнить и поднять `JobNotConfigured`.
- **m7. `hh-selftest` при `JobError` печатает трассировку Python вместо понятного текста.** Новое по объёму, путей теперь больше. `cli.py:34-36`. Секретов в ней нет. Фикс: перехватить `JobError`, вывести `str(e)` и вернуть 1.
- **m8. При `JobLimit` или `JobBlocked` в середине прогона статистика теряется.** Старый долг. `jobs.py:95`. `JobResult(False, JobStats(), …)`: в журнале нет числа новых и изменённых записей и пометки про лимит просмотров, хотя часть данных уже записана. Фикс: брать статистику из задачи (например, через атрибут исключения).
- **m9. Примечание о лимите просмотров затирается.** Новое. `collect.py:58`. Если после него сработает `BudgetExhausted`, `PageBudget.take` перезапишет `stats.note`, и рекрутер не узнает, что полные резюме не запрашивались. Фикс: дописывать к `note`, а не заменять.
- **m10. В тестах `type` ошибок лимита — `limit_exceeded`, а по спеке это `resumes`.** Новое. `test_collect_hh.py`, `test_mark_gone.py`. На результат не влияет, потому что коды объединяются, но фикстуры расходятся с реальным форматом. Фикс: поставить `type="resumes"`.
- **m11. Перепроверка `mark_gone` через `GET /resumes/{id}` расходует тот же суточный лимит просмотров, что и `collect_hh`.** Старый долг, нужна проверка: тарифицирует ли hh повторный просмотр уже открытого резюме. При `hh_recheck_per_run=100` это до 100 просмотров в день. Стоит учесть при выборе порядка задач в расписании.

## Не покрыто тестами (для test-writer)

1. Повторный `token_expired` после успешного обновления → `JobNotConfigured` с «hh-auth», ровно один запрос к `/token` (защита от цикла).
2. 403 `token_expired`, а `/token` отвечает 400 `invalid_grant` → `JobNotConfigured`, без повторов.
3. 401 два раза подряд → одно обновление, потом `JobNotConfigured`.
4. 429 без тела или с неизвестным кодом → `JobBlocked` и `stats.blocked`.
5. 403 с телом не-JSON (HTML) → `JobNotConfigured`, без исключения разбора.
6. Капча в формате спеки: `{"type": "captcha_required", "value": "captcha_required"}` и `{"type": "employer_resume_view", "value": "captcha_required"}`. Сейчас в тесте есть только `type`.
7. `errors[].value = token_revoked` без `oauth_error` (после фикса m2).
8. `selftest`: `/me` не работодателя → `JobNotConfigured`; 403 или 404 на `method_access`; `views_left` отсутствует (поле `resume_view_from_api` не предусмотрено); пустой список услуг.
9. `selftest` с истёкшим токеном и падением следующего запроса: обновлённый токен сохранён (после фикса M1).
10. Пауза не меньше 1 с между POST `/token` и следующим GET (после фикса M2).
11. CLI `hh-selftest`: код возврата 1 при закрытом поиске и 0 при открытом, текст вывода.
12. `mark_gone`: после `view_limit_exceeded` наблюдения других источников с `mode='api'` всё равно обрабатываются (после фикса m1).
13. `JobLimit` (`daily_resume_search_limit_*`) в `mark_gone` и на второй странице `collect_hh`: `crawl_queue` получает `error`, уже принятые резюме сохранены.

## Перед первым запуском проверить

- `HH_EMPLOYER_ID`, `HH_CLIENT_ID`, `HH_REDIRECT_URI` (`http://localhost:<порт>/api/oauth/hh/callback` через туннель, AD-6) и `SECRETS_KEY` заданы в `.env` на VPS.
- Сначала только `hh-selftest`. Если поиск закрыт (код 1), `collect_hh` не запускать: прогон закончится `JobNotConfigured` про оплату или доступ, это безопасно, но бесполезно.
- Для первого `collect_hh` уменьшить `crawl_max_pages`, `crawl_per_profile_pages` и `hh_full_resume_per_run` в `settings`, чтобы не потратить суточный лимит просмотров за один прогон.
- В первые дни не запускать `mark_gone` в один день с `collect_hh`: обе задачи тратят один лимит (m11).

## Что исправлено после ревью (2026-10-28)

- M1: `selftest` берёт токен в своей транзакции (`conn_factory`), обновлённый токен не откатывается. Тест `test_selftest_keeps_refreshed_token_when_later_request_fails`.
- M2: POST `/token` идёт через общий `RateLimiter`. Тест `test_token_refresh_respects_rate_limit`.
- M3: полное резюме запрашивается снова, если в сохранённом нет `skill_set`. Тест `test_resume_saved_after_view_limit_gets_full_text_next_run`.
- m1: `mark_gone` после лимита просмотров не прерывается, остальные API-источники обрабатываются.
- m2: `TOKEN_DEAD` дополнен кодами из спеки.
- m7: CLI `hh-selftest` при `JobError` печатает текст и возвращает 1.
- m10: `type` в тестовых ошибках лимита — `resumes`, как в спеке.
- Остались m3–m6, m8, m9, m11 и непокрытые сценарии — к ревью core на Этапе 3.
