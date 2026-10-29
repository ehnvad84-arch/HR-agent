# Журнал: workplace

<!-- Здесь факты, а не планы. Статусы задач — только в plan.md. -->

## Handoff

<!-- Перезаписывается в конце каждой сессии и итерации. Здесь всё, что нужно следующей сессии с чистым контекстом. -->

- Этап: 2 — разработка
- Последнее сделанное: T1–T16 (миграция, вход, оболочка, «Сегодня», досье, фото, GigaChat, разбор по требованию, решения)
- Следующий шаг: T17 — чат обучения агента
- Важно знать:
  - Ветка от `feature/core`: core ещё не смержен (ждёт T21 и Этапа 3).
  - Реальные данные — база `ruware_hr` на `~/.local/pg16`, порт 55432; после перезагрузки поднимать вручную (память `hh-app-reminder`).
  - В `.env.example` дописать (агенту файл недоступен): `SESSION_KEY`, `WEB_HOSTS`, `GIGACHAT_AUTH_KEY`, `GIGACHAT_SCOPE`, `GIGACHAT_MODEL`, `GIGACHAT_CA_FILE`, `GIGACHAT_OAUTH_URL`, `GIGACHAT_BASE_URL`, `STORAGE_DIR`.
  - Живые вызовы hh и платные контакты в задачах — только на заглушке; живое подтверждение делает человек в T32.
  - Спека 263 строки при ориентире 200: фича объединена с `learning` решением человека; контракт API вынесен в `docs/reference/workplace-api.md`.

## Журнал

<!-- Только добавлять, сверху вниз: дата · задача · что сделано · коммит · отклонения от плана или спеки. -->

- 2026-10-29 · Этап 1 · брейншторм, спека, UI (ui-designer), архитектура и безопасность (backend-architect), план, plan-reviewer: blocked (B1 живой контакт hh в T12/T26, B2 имя в модель в T11) → исправлено правкой плана · Snyk заменён на pip-audit и Dependabot (решение человека)
- 2026-10-29 · T1 · миграция 002_workplace: ops-таблицы, права с записью в agent_change, настройки; накатана на ruware_hr · pytest зелёный, ruff чисто
- 2026-10-29 · T2 · `auth.py`: сессия с HMAC, права `require()`, CSRF и Origin, обход маршрутов без права; общие зависимости вынесены в `deps.py` · pytest зелёный · отклонение: `.env.example` закрыт для агента deny-правилом — имена `SESSION_KEY`, `WEB_HOSTS` (и далее новые) копятся в «Важно знать» Handoff, вписывает человек
- 2026-10-29 · T2b · вход: bcrypt (cost 12, 10–72 байт), `/api/login|logout|me`, блокировка с фиксацией неудач, `hr.cli user-create`; `/api/me` выдаёт новый CSRF-токен (в базе только хеш) · pytest зелёный, pip-audit чисто
- 2026-10-29 · T3 · `web.py`: раздача `server/web/`, CSP и заголовки, TrustedHost по `WEB_HOSTS`, ошибки `{error, message}`, необработанное — без текста исключения, журнал доступа без query · pytest зелёный
- 2026-10-29 · T4 · `views.py` (карточка: имя из кандидата или hh, иначе ярлык; фото — флаг), `GET /api/today`; фикстура `api` в conftest для тестов API · pytest зелёный
- 2026-10-29 · T5 · `views.dossier`, `GET /api/person/{id}` и `/dossier`: места работы из raw по белому списку полей, контакты не выдаются, просмотр `hiring_manager` → `audit_log view` · pytest зелёный
- 2026-10-29 · T6 · `photos.py`: задача `photos` (только https-хосты hh, ≤ 2 МБ, 1 запрос/с, 3 попытки, JPEG ≤ 10 КБ без EXIF, защита от бомбы распаковки), `GET /api/person/{id}/photo`; зависимость Pillow 13.0.0, pip-audit чисто · pytest зелёный
- 2026-10-29 · T6 (живой прогон) · `hr.cli run photos` на ruware_hr: 44 фото из 55 карточек (11 без фото на hh), максимум 10 189 байт
- 2026-10-29 · T7 · `gigachat.py` + ветка в `llm.complete`: OAuth-токен в памяти, обновление за минуту и на 401, один поток, пауза на 429/402, TLS с НУЦ из файла; адреса сверены с OpenAPI (`docs/reference/gigachat.md`): генерация теперь на `api.giga.chat` · отклонение: тесты написаны после кода · pytest зелёный
- 2026-10-29 · T8 · `assist.explain_pair`, `POST /api/person/{id}/explain`: кэш `ops.llm_cache` по входу, проверка `explain.validate`, шаблон при отказе, недоступности и сверх `llm_calls_per_hour`; в модель — только `explain.payload` · pytest зелёный
- 2026-10-29 · T9 · `decisions.py` + `/api/person/{id}/take`, `/api/decision`, `/watch`, `/api/social/{id}/confirm`: кандидат в воронку открытой вакансии по коду профиля или в резерв, основание `candidate_initiated`, `take` без `decision_log`; «не наш» по профилю не трогает `watch`, без профиля — `dropped` · pytest зелёный
- 2026-10-29 · T10 · `funnel.py` + `POST /api/candidate/{id}/status`, `GET /api/vacancy/{id}/funnel`: этапы прототипа, привязка из резерва к вакансии, дата встречи → `interview`, отказ → `reject_reason`, среднее время на этапе · pytest зелёный
- 2026-10-29 · T11 · `actions.py` + `/api/candidate/{id}/draft`, `/api/action/{id}/approve|decline`, промпт `draft_letter.md`: шаблоны на «мы», модель без имени и контактов, `{имя}` подставляет код, ответ со ссылкой, почтой или телефоном → шаблон; одобрение сохраняет итоговый текст и переводит в «Написали»; при `not_obtained` — 409 · pytest зелёный
- 2026-10-29 · T12 · `contact_hh.py` + `/api/candidate/{id}/contact/prepare`, `/contacts`, ветка `open_contact` в `approve` (право `contact.open`): prepare без обращения к hh, свежий резюме → FULL без списания, иначе `get_with_contact` только на хосте `HH_API`, на время вызова `approved` (без двойного списания), 409 при занятой блокировке `source:hh`, значения только в `contact`, `audit_log view_contact` при открытии и показе; `jobs.try_lock` · только заглушка hh, живого вызова не было · pytest зелёный
- 2026-10-29 · T13 · `views.base` + `GET /api/base`, `/api/search`, `/api/vacancy/{id}/matching|candidates`: колонки прототипа, фильтры, сортировка по белому списку (422 на чужую), страницы `base_page_size`; подходящие — по коду профиля, без «не наш» по нему и без dropped, от `watch_threshold` · pytest зелёный
- 2026-10-29 · T14 · `vacancies.py` + `/api/vacancies`, `/api/vacancy`, `/close`, `/reopen` (право `vacancy.edit`): создание только по утверждённому профилю, `origin = recruiter`, закрытие от 200 знаков (и CHECK СУБД), `keep_sourcing` не трогается, повторное открытие → `audit_log edit` · отклонение: тест и код в одном шаге · pytest зелёный
- 2026-10-29 · T15 · `profiles.py` + `/api/profiles`, `/api/profile/{id}` (GET, POST — правка), `/approve` (202, пересчёт `rescore_profile` в фоне через `jobs.run`): правка → версия `x.y+1` с пустыми `approved_*`, проверка весов, условий и географии, `agent_change` на правку и утверждение; база и карточки считают только действующие версии · отклонение: тест и код в одном шаге · pytest зелёный
- 2026-10-29 · T15b · `profiles.preview` (`GET /api/profile/{id}/preview`, право `profile.edit`): баллы по новой и действующей версии без записи, до `preview_max_persons`, входящие и выпадающие; `profiles.approvers` (`GET /api/approvers`) · pytest зелёный
- 2026-10-29 · T16 · `profiles.upload_doc` + `POST /api/profile/{id}/doc` (multipart, право `profile.edit`): только .docx и .pdf по сигнатуре, предел `import_max_file_mb`, файл в `STORAGE_DIR/di/<sha256>`, `job_doc`, черновая версия «по новой ДИ» (state `draft`) с `agent_change`; правленая версия — `pending`; зависимость python-multipart 0.0.32, pip-audit чисто · pytest зелёный
