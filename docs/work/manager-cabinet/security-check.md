# Security-check: manager-cabinet

**Вердикт:** fail <!-- два fail уровня major: A01/M2 (контакты в досье руководителя) и A09/LLM02-вне-модели (токен Телеграма в журнале); blocker нет -->

Дифф: `feature/workplace...feature/manager-cabinet` (HEAD e797376) · Дата: 2026-10-30 · Ревьюер: независимый ревьюер (Claude, субагент).

## Базовый минимум

- [x] В диффе и истории ветки нет секретов: поиск по диффу шаблонов ключей и токенов Телеграма ничего не нашёл; `SESSION_KEY` в тестах — заглушка `"k"*40`.
- [x] `.env` не в индексе (`git ls-files` — нет); `.gitignore`, `.cursorignore` и deny в `.claude/settings.json` на месте (deny сработал на моё чтение `.env.example`).
- [x] Нет доступа к продакшну: прод-ключей, прод-адресов и команд деплоя в диффе нет. `install_autostart.sh` запускает человек.
- [ ] Новые секреты описаны: `FEEDBACK_TG_TOKEN`, `FEEDBACK_TG_CHAT`, `TELEGRAM_API` не добавлены в `.env.example` (security.md п.2) — minor, находка 6 в review.md.
- [x] Минимальные права: матрица прав в `007_manager_cabinet.sql` совпадает с таблицей спеки; у руководителя нет `contact.open`, `dossier.export`, `person.take`, `collect.run`, `base.all`.
- [x] Сканеры запущены (ниже).

## Сканеры

| Инструмент | Команда | Результат |
|---|---|---|
| Snyk — зависимости (SCA) | `snyk test` | не запускался: Snyk не установлен и заменён решением человека 2026-10-29 (security.md п.5) |
| Snyk — код (SAST) | `snyk code test` | не запускался (та же причина) |
| pip-audit (замена Snyk SCA) | `pip-audit -r requirements.txt` | No known vulnerabilities found; `requirements.txt` в диффе не менялся |
| ruff | `ruff check .` | All checks passed |
| agentshield | `agentshield scan --path .` | не требовался: `.claude/`, хуки, MCP, скиллы и плагины в диффе не менялись |
| Claude Code Security / `/security-review` | запускает главная сессия | — |

## Правила проекта (docs/rules/security.md, never.md)

| Правило | Результат | Где |
|---|---|---|
| Права проверяются на сервере по `role_permission` | pass | `auth.require`, `auth.require_scoped`; страж `routes_unscoped` = [] (`test_scope_points.py::test_every_base_read_route_is_scoped`) |
| Открытие контакта, выгрузка досье, просмотр карточки руководителем — в `audit_log` | pass | `api/person.py:_read` — `view` для всех без `base.all`; выдача кода — `invite` (`access.py:issue_code`) |
| Текст резюме, сообщений, расшифровок — недоверенные данные | pass | предложения поправок — регулярное выражение плюс белый список `tweaks.clean`, `state = proposed`; в модель уходит `mask(body)` |
| Пароли — bcrypt; сессия `HttpOnly`, `Secure`, `SameSite=Lax` | pass | `login_by_code` → `auth._hash_password`; cookie в `/api/login/code` как в `/api/login` |
| В логи не пишутся контакты, ФИО, тексты резюме | pass | `load_check`, `staff_1c`, `feedback_week` пишут только счётчики, класс ошибки и логины сотрудников |
| Секреты не в логах | **fail** (major) | `telegram.py:47` + `jobs.py:135`: URL с токеном бота попадает в трассировку `log.exception` |
| Не отправлять контакты кандидатов в Telegram | pass с оговоркой | замечания маскируются (`mask` + `_FIO_LIKE`); заголовки `digest_proposal` идут без маски (`learning.py:341`, minor) |
| Не передавать ФИО и контакты в запрос к модели | pass | `learning.post` — прежняя маска; новых вызовов модели нет |
| Не выполнять действие с человеком без подтверждения | pass | «Взять» по передаче → `decisions.take`, контакт — по прежней кнопке с подтверждением |
| AD-3 (уточнение): руководителю — ФИО и фото, без контактов и ссылок | **fail** (major) | `views.for_manager` (`views.py:120`) оставляет `observations[].about` и `experience[].description` — свободный текст с контактами (подтверждено тестом) |

## OWASP Top 10 (2021)

| Пункт | Результат | Где |
|---|---|---|
| A01 Broken Access Control | **fail** (major) | Горизонтальный доступ закрыт: скоуп на сервере, 404 вне скоупа в каждой точке из таблицы спеки, страж маршрутов, `_own` (403/404) для профилей, передачи только своих или по `handoff.all`, уведомления — `user_id = me`. Утечка контактов через свободный текст досье руководителя (M2) обходит границу зон AD-3. Второе: без ежедневной выгрузки уволенный руководитель сохраняет доступ до ручного «Обновить из 1С» (M5, major) |
| A02 Cryptographic Failures | pass | код — `secrets.choice`, 32^10 ≈ 50 бит, в базе HMAC-SHA256 от `SESSION_KEY`, сравнение `hmac.compare_digest`; пароль — bcrypt |
| A03 Injection | pass | SQL с плейсхолдерами `%s`; сортировка через белый список `SORTS`; в шаблон SQL подставляются только константы (`CURRENT`, `_LIST.format(where=…)` — литералы); во фронте нет `innerHTML`, `href` строятся из `hashFor`/`personHref` |
| A04 Insecure Design | pass с оговоркой | модель угроз M1–M7, A1–A4 есть в спеке; блокировка кода позволяет постороннему каждые 15 минут мешать первому входу и узнавать логины с живым кодом (429 против 401) — minor |
| A05 Security Misconfiguration | pass | новых открытых маршрутов — один (`/api/login/code`, с проверкой `Origin`); остальные с CSRF; загрузка выгрузки ≤ 5 МБ, имя файла в путь не идёт |
| A06 Vulnerable and Outdated Components | pass | pip-audit чист, зависимостей не добавлено |
| A07 Identification and Authentication Failures | pass с оговоркой | один ответ на неверный, истёкший и погашенный код и неизвестный логин (401); bcrypt до проверки кода выравнивает время; сброс пароля по коду не гасит прежние сессии (minor) |
| A08 Software and Data Integrity Failures | pass | выгрузка 1С: sha256, всё или ничего, отказ при пропаже > `users_max_drop_pct`, роль `agent` и `user_override` не трогаются, изменения в `agent_change` |
| A09 Security Logging and Monitoring Failures | **fail** (major) | аудит (`audit_log`, `agent_change`) полный; но токен Телеграма может попасть в журнал (`telegram.py:47`) |
| A10 SSRF | pass | `TELEGRAM_API` — из `.env`, а не из запроса; других исходящих адресов из ввода нет |

## OWASP LLM Top 10 (2025)

| Пункт | Результат | Где |
|---|---|---|
| LLM01 Prompt Injection | pass | новых промптов нет; предложения поправок не от модели (регулярное выражение), только `proposed`, белый список `tweaks.clean` (вид, 2–60 знаков, без ссылок и `<`, признак из `FEATURE_LABELS`, вес 0–100) |
| LLM02 Sensitive Information Disclosure | pass с оговоркой | `learning.feed` по автору (S14, A3); в модель — маска; для внешних каналов см. заголовки предложений без маски (minor) |
| LLM03 Supply Chain | n/a | модель и провайдер не менялись |
| LLM04 Data and Model Poisoning | pass | общий портрет меняется только через сводку → `digest.decide` → версия → `profile.approve` (A2); `safe_target` пропускает вид `tweak` только после `tweaks.clean` |
| LLM05 Improper Output Handling | pass | ответ модели в новых путях не используется; на фронте вывод через `textContent` |
| LLM06 Excessive Agency | pass | у модели нет инструментов и прав; поправка действует только после кнопки автора |
| LLM07 System Prompt Leakage | n/a | системные промпты не менялись |
| LLM08 Vector and Embedding Weaknesses | pass | `/api/search` у руководителя фильтруется `scope.person_ok` после `search.similar` |
| LLM09 Misinformation | n/a | балл считает код (AD-2) |
| LLM10 Unbounded Consumption | pass | `POST /api/person/{id}/explain` доступен руководителю только в скоупе, прежний `llm_calls_per_hour` действует; новых вызовов модели нет |

## OWASP Agentic AI Threats

| Пункт | Результат | Где |
|---|---|---|
| T1 Memory Poisoning | pass | личные поправки изолированы по `user_id`; в общее — только через решение человека |
| T2 Tool Misuse | n/a | у агента нет инструментов в этой фиче |
| T3 Privilege Compromise | pass | права читаются на каждый запрос; смена роли гасит сессии (`access.update`, `staff_1c`); специалист выдаёт код только руководителю или директору без пароля (M4, `test_m4_recruiter_cannot_hijack`) |
| T4 Resource Overload | pass с оговоркой | `rescore_person_all` в транзакции сбора — нагрузка растёт с числом авторов поправок (minor, нужна проверка на объёме) |
| T5 Cascading Hallucination Attacks | n/a | |
| T6 Intent Breaking & Goal Manipulation | pass | команда в тексте замечания даёт максимум `proposed`-поправку из белого списка |
| T7 Misaligned & Deceptive Behaviors | n/a | |
| T8 Repudiation & Untraceability | pass | передачи (`from_user`, `decided_by`), коды (`audit_log invite`), роли (`agent_change access`), «Разбираюсь» с именем |
| T9 Identity Spoofing & Impersonation | pass с оговоркой | код видит выдавший HR и может войти им сам (по спеке, M4 ограничивает круг); блокировка кода посторонним — minor |
| T10 Overwhelming Human in the Loop | pass | тревоги о загрузках не чаще `load_alarm_repeat_hours` |
| T11 Unexpected RCE and Code Attacks | n/a | |
| T12 Agent Communication Poisoning | n/a | один агент |
| T13 Rogue Agents in Multi-Agent Systems | n/a | |
| T14 Human Attacks on Multi-Agent Systems | n/a | |
| T15 Human Manipulation | pass | агент ничего не делает с людьми; передачу разбирает человек |

## MITRE ATLAS (модель угроз спеки A1–A4)

A1 — pass (нет модели в пути, белый список, `proposed`). A2 — pass (сводка → решение → версия → утверждение). Оговорка: сквозной путь S16 через API не создаёт версию, см. review.md, находка 4. A3 — pass (`feed` по автору, тест S14). A4 — pass (вызова модели на поправки нет).

## Находки

| # | Уровень | Где | Проблема и риск | Минимальный фикс |
|---|---|---|---|---|
| 1 | major | `server/hr/views.py:120`, `:140`, `:158` | Руководитель видит телефон и почту из `observations[].about` и `experience[].description` и выходит на кандидата в обход HR (M2, AD-3). Подтверждено тестом | маскировать (`learning.mask`) или убирать эти поля в `for_manager`; тест |
| 2 | major | `server/hr/telegram.py:47`, `server/hr/jobs.py:135` | `HTTPStatusError` содержит URL с токеном бота → трассировка в journald | ловить `httpx.HTTPError`, бросать `JobError` без URL |
| 3 | major | `server/hr/tasks.py:74`, `server/scripts/install_autostart.sh` | Нет ежедневной выгрузки сотрудников: уволенный сохраняет доступ до ручного нажатия (M5, S1) | задача `staff_sync` и таймер |
| 4 | minor | `.env.example` | Нет `FEEDBACK_TG_TOKEN`, `FEEDBACK_TG_CHAT`, `TELEGRAM_API` | добавить имена |
| 5 | minor | `server/hr/access.py:152-155` | 429 против 401 выдаёт логины с живым кодом; посторонний может блокировать первый вход | одинаковый ответ, счётчик по логину |
| 6 | minor | `server/hr/access.py:166` | Сброс пароля по коду не гасит прежние сессии | отозвать сессии при успешном входе по коду |
| 7 | minor | `server/hr/learning.py:338-341` | Заголовки предложений уходят в Телеграм и на почту без маски | `mask` + `_FIO_LIKE` для `title` |
