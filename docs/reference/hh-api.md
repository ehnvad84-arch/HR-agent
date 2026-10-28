# API hh.ru: что нам нужно и где мы расходимся

Конспект спецификации OpenAPI hh (https://api.hh.ru/openapi/redoc), прочитанной 2026-10-27. Здесь только то, что касается нашей схемы: обезличенная выборка → отбор рекрутером → точечное открытие контакта. Остальное API (вакансии, чаты, звонки, вебхуки) — по ссылке, когда понадобится.

## Общие правила

- Базовый адрес `https://api.hh.ru/`, только JSON. Заголовок `User-Agent` (или `HH-User-Agent`) обязателен: «Название/версия (почта разработчика)». Без него — 400.
- Пагинация: `page` с нуля, `per_page`. Для поиска резюме `per_page` до 100, глубина выдачи не больше 2000 (`page * per_page < 2000`).
- Неизвестные параметры запроса hh **молча игнорирует**. Опечатка в имени параметра не даёт ошибки, а расширяет выборку.
- Ошибки приходят телом `{"request_id": "...", "errors": [{"type": ..., "value": ...}]}`. Разбирать нужно `type` и `value`, а не текст.

## Авторизация

Подробные требования: https://api.hh.ru/openapi/redoc#section/Avtorizaciya

- Вход пользователя: `https://hh.ru/oauth/authorize?response_type=code&client_id=…&state=…&redirect_uri=…`. Поддерживается PKCE (`code_challenge`, `code_challenge_method=S256`, при обмене `code_verifier`).
- `redirect_uri` должен совпадать с сохранённым в приложении по протоколу, домену и порту. Указать порт, которого нет в настройках, нельзя. Допускается уточнение пути и дополнительные параметры. У нас сохранён `http://localhost:8000/api/oauth/hh/callback`.
- Параметры `role=employer&force_role=true&skip_choose_account=true` сразу просят вход работодателя.
- Обмен кода: `POST /token`, форма `grant_type=authorization_code, client_id, client_secret, redirect_uri, code`. Код живёт недолго и используется один раз.
- `access_token` живёт `expires_in` секунд (в примере 1 209 600, то есть 14 дней). Обновить его можно **только после истечения**, иначе `invalid_grant: token not expired`. `refresh_token` одноразовый: повторное использование даёт `token has already been refreshed`.
- Истёкший или отозванный токен hh возвращает **403** (`oauth_error: token-expired` / `token-revoked`, `errors[].value: token_expired`), а не 401.
- Токен приложения (`grant_type=client_credentials`) бессрочный, запрашивается не чаще раза в 5 минут. Резюме по нему смотреть нельзя: нужен токен пользователя-работодателя.
- `DELETE /token` отзывает токен. При компрометации: отозвать токен и пройти вход заново.
- Если у менеджера несколько рабочих аккаунтов, нужный аккаунт выбирается заголовком `X-Manager-Account-Id` (список: `GET /manager_accounts/mine`).

## Кто мы: `GET /me`

Наша учётная запись (по `/me`, 2026-10-28): v.prisyazhnyuk@nic-tech.ru, user id `66950394`, **employer id `1473839`**, **manager id `4005896`**, персональный менеджер hh `83733351`. В `.env` на VPS: `HH_EMPLOYER_ID=1473839`.

Для работодателя: `auth_type=employer`, `is_employer=true`, `employer.id`, `manager.id`. Обе идентичности нужны: `employer_id` и `manager_id` входят в путь методов лимитов и доступа.

## Доступ к базе резюме и лимиты

- **Поиск и просмотр резюме через API требуют платного доступа к базе резюме у работодателя.** Без него `GET /resumes` отвечает 403 `api_access_payment / action_must_be_payed`. Кроме того, бывает отдельная услуга «Доступ к API HH» (`API_UNLIMITED`) или пакет запросов (`API_LIMITED`, с балансом).
- Что доступно, показывает `GET /employers/{employer_id}/managers/{manager_id}/method_access`: группа 4 — «просмотр резюме, найденных через поиск по базе», группа 3 — «резюме с откликом или приглашением».
- Активные платные услуги: `GET /employers/{employer_id}/services/payable_api_actions/active`.
- Суточный лимит просмотров резюме менеджера: `GET /employers/{employer_id}/managers/{manager_id}/limits/resume` → `limits`, `spend`, `left` по `resume_view` (сайт) и `resume_view_from_api`. Лимит считается на компанию, поэтому `left` бывает меньше личного остатка менеджера.
- Исчерпание лимитов возвращает **429**:
  - просмотр резюме: `errors[].value = view_limit_exceeded`;
  - поиск: `daily_resume_search_limit_exceeded` или `daily_resume_search_limit_without_services_exceeded`. У поиска через API свой суточный лимит, отдельный от просмотров.
- Капча: 403 с `errors[].type = captcha_required` и `captcha_url`.

## Поиск: `GET /resumes`

Полезные параметры:

| Параметр | Что даёт |
|---|---|
| `text`, `text.logic`, `text.field`, `text.period` | поисковая фраза; три уточняющих параметра указываются вместе для каждого `text` |
| `area`, `relocation` | регион; по умолчанию hh возвращает живущих в регионе и готовых переехать; `relocation=living` — только живущих |
| `work_format` | формат работы из `resume_work_format`: `ON_SITE`, `REMOTE`, `HYBRID`, `FIELD_WORK`, `FLY_IN_FLY_OUT` |
| `employment_form` | тип занятости (`resume_employment_form`) |
| `experience`, `professional_role`, `skill`, `education_levels` | фильтры по опыту, роли, навыку (id из подсказок), образованию |
| `period` или `date_from`/`date_to` | только резюме, обновлённые за период: основа инкрементального обхода |
| `order_by` | `publication_time`, `relevance`, `salary_desc`, `salary_asc` |
| `job_search_status` | фильтр по статусу поиска работы |
| `with_job_search_status=true` | вернуть в выдаче `job_search_status` (иначе поля нет) |
| `label` | `only_with_salary`, `only_with_age`, `exclude_viewed_by_employer_id` и др. |
| `vacancy_id`, `resume` | резюме, похожие на вакансию или на другое резюме |

Выдача группируется по соискателю: один человек — одно резюме в выдаче. Элемент выдачи без контактов: `first_name`/`last_name` равны `null`, `contact_view_status = NONE`, `can_view_full_info = false`. Есть `owner.id` (идентификатор соискателя), `title`, `area`, `age`, `salary`, `total_experience.months`, `experience[]` без описания, `education`, `updated_at`, `job_search_status` (по флагу), `actions.get_with_contact.url`.

## Просмотр: `GET /resumes/{id}`

- Полное резюме: описания опыта, `skill_set`, `skills`, языки, `relocation`, `work_ticket`, `hidden_fields`.
- Каждый вызов тратит суточный лимит просмотров. Пока контакты никто в компании не открывал, ФИО, `contact`, `site`, `photo` приходят `null`.
- `404` — резюме удалено или скрыто от нас: это основание для «пропал».
- Параметры: `with_job_search_status=true`, `with_negotiations_history=true` (три последние вакансии с нашими откликами), `with_creds=true`.

## Открытие контакта: списывает услугу

- Ссылки в `actions.get_with_contact.url` и `actions.download_with_contact` есть, только если у компании есть оплаченная услуга открытия контактов и контакт ещё не открыт. Вызов ссылки списывает один контакт.
- Приглашение соискателя на вакансию тоже списывает контакт. Если соискатель откликнулся сам, контакт открыт без списания.
- Списание за резюме бывает один раз на компанию: после открытия контакт видят все менеджеры, пока действует услуга.
- **Если соискатель скрыл контакты, просмотр всё равно спишется**, а поля придут `null`. Перед открытием проверять `hidden_fields`: `names_and_photo`, `phones`, `email`, `other_contacts`.
- `contact_view_status`: `FULL` — контакты видны, `IPC` — общение только внутри hh, `NONE` — недоступны.
- `GET /resumes/{id}/phone` — номер для звонка без списания: реальный, если контакты уже доступны, или временный виртуальный номер в режиме IPC (звонить до `expires_at`).

## Что пригодится в следующих фичах

- `GET /suggests/skill_set`, `/suggests/positions`, `/suggests/professional_roles`, `/suggests/resume_search_keyword` и справочники `GET /dictionaries`, `/professional_roles`, `/areas` помогут в профилях должностей (`workplace`): навыки и роли по id вместо свободного текста.
- `GET /resumes/{id}/negotiations_history`, комментарии к соискателю `/applicant_comments/{owner.id}`: связь с перепиской компании на hh.
- Вакансии компании, отклики (`/negotiations`), приглашение (`POST /negotiations/phone_interview`), чаты (`/common/chats`). Это действия с человеком: только через `action_queue` и кнопку (never.md).
- Вебхуки (`/webhook/subscriptions`) требуют публичного адреса. При схеме AD-6 (наружу только SSH) недоступны, остаётся опрос.
- Сохранённые поиски (`/saved_searches/resumes`) со счётчиком `new_items`: альтернатива нашему обходу, но счётчик обновляется раз в час.

## Расхождения с нашим кодом (к ревью core)

1. **Удалёнка не фильтруется.** `hh.search_params` для `geo = remote_ru` шлёт `schedule=remote`. У поиска резюме нет параметра `schedule` (он есть только у вакансий и устарел), hh его игнорирует, и выдача идёт по всей России. Нужно `work_format=REMOTE`.
2. **Истёкший токен принимается за отсутствие доступа.** `Api.get` обновляет токен только на 401, а hh отвечает 403 с `token_expired`. Сейчас это даст `JobNotConfigured("нет доступа к базе резюме")` вместо обновления. Разбирать `errors[].value` и `oauth_error`.
3. **Исчерпанный лимит выглядит как капча.** 429 с `view_limit_exceeded` или `daily_resume_search_limit_exceeded` даёт `JobBlocked` с текстом про проверку на робота. Нужен отдельный исход «лимит на сутки исчерпан», а при `view_limit_exceeded` — продолжать поиск без полных резюме.
4. **`job_search_status` не приходит.** `normalize` его читает, но поиск не передаёт `with_job_search_status=true`. Сигнал «человек начал искать работу» сейчас не работает.
5. **`owner.id` не используется для сшивки.** Это устойчивый идентификатор соискателя на hh для всех его резюме. Его можно приравнять к нику (вес 1.0) для двух резюме одного человека на hh.
6. **Учёт лимита просмотров можно взять у hh.** Вместо собственного счёта за сутки (задача `deploy`) читать `limits/resume` перед прогоном и ставить `hh_full_resume_per_run` не выше `left.resume_view_from_api`. Для этого при входе сохранять `manager.id`, сейчас сохраняется только `employer.id`.
7. **`hh-selftest` проверяет мало.** Добавить `method_access` (есть ли доступ к поиску по базе), `payable_api_actions/active` и `limits/resume`. Тогда отсутствие платного доступа видно до первого ночного прогона.
8. **Мелочи:** `per_page=100` вместо 50 вдвое сокращает страницы поиска. `date_from` с прошлого прогона вместо полного обхода каждую ночь. PKCE в `auth_link` дешёвый и добавляет защиту. Капчу определять по `type = captcha_required`, а не по подстроке в тексте.
9. **Открытие контакта (фича `workplace`).** Кнопка «открыть контакт» вызывает `actions.get_with_contact.url` из свежего ответа `/resumes/{id}`. Перед вызовом проверить `hidden_fields` и предупредить рекрутера, что контакт скрыт и списание пройдёт впустую. Сначала пробовать `/resumes/{id}/phone`: он ничего не списывает.
