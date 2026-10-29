# API рабочего места

Контракты эндпойнтов фичи `workplace`. Общие правила (формат ошибок, CSRF, права) — `docs/work/workplace/feature-spec.md`, разделы «Архитектура» и «Безопасность».

| Эндпойнт | Право | Вход → выход | Особые ошибки |
|---|---|---|---|
| `POST /api/login`, `POST /api/logout`, `GET /api/me` | — / сессия | `{login, password}` → cookie, `{user, permissions[], csrf}` | 401 неверно (текст один для логина и пароля), 429 блокировка |
| `GET /api/today` | `base.read` | → `{signals[≤daily_cards_limit], actions[], stalled[], meetings[]}`; карточка: `person_id, name\|label, photo, score, headline` | — |
| `GET /api/person/{id}`, `/dossier` | `base.read` | → карточка, `match[]` с `features` «сколько из скольких», версии `obs_version`, `social[]`, события, решения, `contact_opened` (без значений) | 404 |
| `GET /api/person/{id}/photo` | `base.read` | → `image/jpeg` ≤ 10 КБ | 404 → заглушка в интерфейсе |
| `POST /api/person/{id}/explain` | `base.read` | `{role_profile_id}` → `{text, by: model\|template}` | никогда не 5xx: отказ модели = шаблон (S8) |
| `POST /api/person/{id}/take` | `person.take` | `{vacancy_id}` → `{candidate_id}` | 409 уже кандидат |
| `POST /api/decision` | `person.take` | `{person_id, kind: postpone\|reject, role_profile_id, comment}` | 422 комментарий < `decision_comment_min` |
| `POST /api/person/{id}/watch`, `POST /api/social/{id}/confirm` | `person.take` | `{on: bool}` / — → `{watch}` / `{confidence}` | 409 профиль `rejected` |
| `POST /api/candidate/{id}/status` | `person.take` | `{vacancy_id, to_status, comment?, scheduled_at?}` → `{status}`; `scheduled_at` → `interview` | 422 неизвестный этап |
| `POST /api/candidate/{id}/contact/prepare` | `contact.open` | → `{action_id, hidden: [...], already_open, will_spend}` | 409 нет кандидата / не hh |
| `GET /api/candidate/{id}/contacts` | `contact.open` | → `contacts[]`; каждый показ — `audit_log view_contact` | 404 |
| `POST /api/candidate/{id}/draft` | `person.take` | `{vacancy_id, kind}` → `{action_id, text, by}` | 409 основание `not_obtained` |
| `POST /api/action/{id}/approve` | `person.take`; `open_contact` — `contact.open` | → `{state: executed, result}`; письмо: статус `first_message_sent`; контакт: вызов hh | 409 не `proposed`; 502/503 отказ hh (S17) |
| `POST /api/action/{id}/decline` | `person.take` | `{reason}` непустая → `{state: declined}` | 422 |
| `GET /api/base`, `GET /api/vacancy/{id}/matching`, `/candidates` | `base.read` | `dept, city, role, score_min, where, source, sort, dir, page` → `{items, total}`; сортировка — только из белого списка колонок | 422 неизвестная колонка |
| `GET /api/search?q=` | `base.read` | → `items[]` по близости | 422 пустой запрос |
| `GET /api/vacancies`, `POST /api/vacancy` | `base.read` / `vacancy.edit` | `{role_profile_id, title, headcount, target_date}` → `{id}`, `origin = 'recruiter'` | 422 профиль не утверждён |
| `POST /api/vacancy/{id}/reopen` | `vacancy.edit` | → `{state: open}`, `audit_log edit` | 409 уже открыта |
| `POST /api/vacancy/{id}/close` | `vacancy.edit` | `{comment}` → `{state: closed}`, `keep_sourcing` не трогается | 422 < 200 знаков; 409 уже закрыта |
| `GET /api/vacancy/{id}/funnel` | `base.read` | → `stages[{status, people[], avg_days}]` | 404 |
| `GET /api/profiles?q=`, `GET /api/profile/{id}` | `base.read` | → `[{id, code, title, version, state: approved\|pending\|draft}]` | — |
| `POST /api/profile/{id}` | `profile.edit` | поля профиля → `{id новой версии, state: pending}` | 422 веса, условия must/stop |
| `GET /api/profile/{id}/preview` | `profile.edit` | → `{passes_now, passes_new, enter[], leave[]}` (до `preview_max_persons`) | 409 версия уже утверждена |
| `POST /api/profile/{id}/approve` | `profile.approve` | → 202 `{job_run_id}`; пересчёт в фоне, «Схема» показывает ход | 403 (32); 409 уже утверждена |
| `POST /api/profile/{id}/doc` | `profile.edit` | multipart `.docx\|.pdf` ≤ `import_max_file_mb` → `{job_doc_id, draft_version_id}` | 422 тип или размер |
| `GET/POST /api/feedback` | `base.read` / `feedback.write` | `{body, tag, person_id?, role_profile_id?}` → `{id, reply, by}` | 422 < `feedback_min_chars` |
| `GET /api/digest/current`, `POST /api/digest/{proposal_id}/decide` | `base.read` / `digest.decide` | `{decision: accepted\|declined\|discuss, note}` → `{decision}` | 409 уже решено |
| `GET /api/changelog`, `GET /api/report/weekly`, `POST /api/report/weekly/send` | `report.read` | `from, to` → записи `agent_change`; send → `{sent_at}` | 502 SMTP (отчёт сохранён) |
| `GET /api/approvers`, `GET /api/report/recipients`, `GET /api/schema` | `base.read` / `report.read` / `base.read` | → роли и пользователи с `profile.approve` / адреса `MAIL_TO` / последний прогон каждой задачи из `ops.job_run` | — |
| `GET /api/person/{id}/export?f=docx` и `?f=pdf` | `dossier.export` | docx → файл; pdf → `{export_id}`, дальше печать в браузере | 403; 404 |
