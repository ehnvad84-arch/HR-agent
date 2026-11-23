# Security-check: funnel-report

**Вердикт:** pass. Ни одного fail; находки — minor.

Дифф main...feature/funnel-report, 2026-11-23. Применимые фреймворки по `docs/rules/security.md`: OWASP Top 10, OWASP LLM Top 10, OWASP Agentic AI Threats, MITRE ATLAS, NIST AI RMF.

## Базовый минимум

- [x] **Секретов в диффе и истории ветки нет.** Проверил grep по диффу: password, secret, key, token, приватные ключи. Пароль демо-пользователей случайный на каждый прогон (`scripts/demo_funnel.py:36`, `secrets.token_urlsafe`). Адреса — только локальная тестовая база в журнале.
- [x] **`.env` защищён.** В индексе только `.env.example`. `.gitignore:10-12`, `.cursorignore:1-3` и deny в `.claude/settings.json:3` на месте. Новых переменных нет.
- [x] **Доступа к продакшну нет.** Прод-ключей, прод-адресов и команд деплоя нет. `ОБНОВЛЕНИЕ_портала.md` раздел 8 — инструкция человеку.
- [x] **Права минимальные.** Новое право `stage.approve` есть только у `head_recruiter` (`012:218`), его выдача записана в `agent_change` (`012:213-216`). У `agent` нет `person.take`, `vacancy.edit` и `stage.approve` (schema:765).
- [x] **Сканеры запущены**, результаты ниже.

## Сканеры

| Инструмент | Команда | Результат |
|---|---|---|
| Snyk — зависимости (SCA) | `snyk test` | не запускался: Snyk заменён решением человека 2026-10-29 (security.md п.5) на `pip-audit` и Dependabot |
| Snyk — код (SAST) | `snyk code test` | не запускался (так же) |
| pip-audit | `pip-audit -r requirements.txt` | `No known vulnerabilities found`; `requirements.txt` в диффе не менялся |
| ruff (включая правила S) | `ruff check .` | `All checks passed!` |
| agentshield | `agentshield scan --path .` | не требуется: `.claude/`, хуки, MCP, скиллы и плагины в диффе не менялись |
| Claude Code Security / `/security-review` | запускает главная сессия | — |

## OWASP Top 10 (2025)

| Пункт | Результат | Где |
|---|---|---|
| A01 Broken Access Control | pass | Подробности после таблицы |
| A02 Security Misconfiguration | pass | Новых настроек сервера нет. `_error` (`hr/web.py:46`) отдаёт только ключи, которые положил код (`slot`, `count`), а не исключения |
| A03 Software Supply Chain Failures | pass | Зависимостей не добавлено, `pip-audit` чистый |
| A04 Cryptographic Failures | n/a | Криптография не менялась |
| A05 Injection | pass | Подробности после таблицы |
| A06 Insecure Design | pass | Исход пишется один раз, отмена ставит пометку и не удаляет строку, у отметки снимок строки отчёта. Одна открытая отметка на пару и один `proposed` на вакансию держит EXCLUDE в СУБД. Гонки закрывает `FOR UPDATE` вакансии и `stale` |
| A07 Authentication Failures | pass | Вход не менялся |
| A08 Software or Data Integrity Failures | pass | Подробности после таблицы |
| A09 Security Logging and Alerting Failures | pass | Подробности после таблицы |
| A10 Mishandling of Exceptional Conditions | pass (minor) | Две ветки дают 500 вместо 409: гонка дубля этапа (`stages.py:89-92`) и висящая пауза после отката (`vacancies.py:142-151`). Данные не портятся. См. review.md, находки 9 и 10 |

**A01 Broken Access Control:**
- Запись идёт через `auth.require`/`require_scoped`: проверяются сессия, право и CSRF (`auth.py:188-205`; тест `test_movement_needs_rights` — без CSRF 403).
- Чтение — через `require_scoped`: вакансия вне скоупа отдаёт 404. `stage-sets/{id}` проверяется по своей вакансии (`api/stages.py:118-126`).
- Отчёты:
  - рекрутер видит только `recruiter_id` = он;
  - чужие отчёты и отдел — только при `report.read` + `scope.all`;
  - директор без `base.all` получает 404 (`api/stages.py:208-251`; тесты `test_s11`/`test_s12`/`test_s13`).
- Замки `test_stage_routes_scoped_and_with_permission` и `test_movement_and_report_routes_locked` зелёные.
- Отметки в чужой вакансии (T10) — вопрос дизайна, не обход прав (review.md, находка 8).

**A05 Injection:**
- SQL только с `%s` и именованными плейсхолдерами. Конкатенации пользовательского ввода в SQL нет (`stages.py`, `funnel_report.py`, `views.py`, `funnel.py`).
- XSS: в новом JS нет `innerHTML`/`insertAdjacentHTML`/`eval`. Названия этапов, подписи и комментарии выводятся через `h()`/`textContent`.

**A08 Software or Data Integrity Failures:**
- Миграция 012 только добавляет: линтер DDL зелёный, `compat_check.sh v1.2.0` дал 687 passed.
- `funnel_carry` идемпотентна (тест `test_s15_carry_repeat_changes_nothing`).
- Целостность счёта: в счёт идут только неотменённые отметки, отчёт после отмены равен отчёту до ошибки (`test_s16_cancelled_marks_not_counted_and_undo_restores_report`).
- Изъян S16 в интерфейсе (review.md, находка 1) — функциональный, а не уязвимость: ошибочный отказ остаётся в отчёте, пока его не отменят через API.

**A09 Security Logging and Alerting Failures:**
- Отмена пишется в `audit_log` `edit` `candidate_stage` (`stages.py:510-514`); пауза, снятие паузы и повторное открытие — `audit_log` `edit` `vacancy` (`vacancies.py` `_audit`).
- Справочник и наборы пишутся в `agent_change` kind `stages`; право — в `agent_change` kind `permission`.
- В логи приложения не пишутся ни ФИО, ни контакты, ни комментарии: новых `logging`/`print` в `hr/` нет.

## OWASP LLM Top 10 (2025)

Модель в этой фиче не вызывается. `stages.py`, `funnel_report.py` и `api/stages.py` не импортируют `llm`, фоновые задачи таблиц этапов не трогают.

| Пункт | Результат | Где |
|---|---|---|
| LLM01 Prompt Injection | n/a | Тексты отметок и наборов в промпт не идут |
| LLM02 Sensitive Information Disclosure | pass | Комментарии отметок руководителю подразделения уходят через `strip_contacts` (`api/funnel.py:62-66`, тест `test_manager_board_read_only_and_comments_without_contacts`). Отчёты — только числа и названия вакансий |
| LLM03 Supply Chain | n/a | Модель не менялась |
| LLM04 Data and Model Poisoning | n/a | Обучения и векторов в фиче нет |
| LLM05 Improper Output Handling | n/a | Вывода модели в фиче нет |
| LLM06 Excessive Agency | pass | Этапы, итоги и наборы ставит только человек; у `agent` нет прав записи (тесты `test_agent_writes_no_stages`, `test_movement_needs_rights`). Одобренное письмо меняет только статус (`actions.py:164-166`) |
| LLM07 System Prompt Leakage | n/a | — |
| LLM08 Vector and Embedding Weaknesses | n/a | — |
| LLM09 Misinformation | pass | Счёт — SQL по отметкам людей (AD-2), без оценок и подсветки «хорошо/плохо» |
| LLM10 Unbounded Consumption | n/a | Вызовов модели нет. Размер ввода API не ограничен (review.md, находка 13, minor) |

## OWASP Agentic AI Threats

| Пункт | Результат | Где |
|---|---|---|
| T1 Memory Poisoning | n/a | Памяти агента фича не касается |
| T2 Tool Misuse | pass | У агента нет инструментов записи этапов: маршруты требуют `person.take`/`vacancy.edit`/`stage.approve` |
| T3 Privilege Compromise | pass | `stage.approve` есть только у `head_recruiter`. `undo` сам проверяет право по `role_permission` (`stages.py:467-469, 495-496`) |
| T4 Resource Overload | n/a | Фоновых задач нет. Отчёт отдела — один SQL на набор вакансий плюс запрос раскрытия на вакансию; на объёме отдела этого достаточно |
| T5 Cascading Hallucination Attacks | n/a | Модели нет |
| T6 Intent Breaking & Goal Manipulation | n/a | — |
| T7 Misaligned & Deceptive Behaviors | n/a | — |
| T8 Repudiation & Untraceability | pass | У отметок есть `entered_by`/`decided_by`/`cancelled_by` с причиной; отмена и пауза пишутся в `audit_log`, наборы — в `agent_change`. Утверждение руководителем ОП собственного набора видно в журнале |
| T9 Identity Spoofing & Impersonation | pass | Сессия и CSRF прежние, новых каналов входа нет |
| T10 Overwhelming Human in the Loop | pass | На утверждении у вакансии не больше одного набора. У руководителя ОП — счётчик и один блок «Ждут утверждения» |
| T11 Unexpected RCE and Code Attacks | n/a | Исполнения кода из данных нет |
| T12 Agent Communication Poisoning | n/a | — |
| T13 Rogue Agents in Multi-Agent Systems | n/a | — |
| T14 Human Attacks on Multi-Agent Systems | n/a | — |
| T15 Human Manipulation | n/a | Агент не участвует в коммуникации по этапам |

## MITRE ATLAS / NIST AI RMF

- **ATLAS — n/a.** Модель в воронке не участвует (спека, «Угрозы»); подтверждено чтением кода.
- **NIST AI RMF — n/a для фичи.** Риск «модель исказит методику» (опасение ОП) закрыт архитектурно: AD-2 и раздел 4 в review.md.

## Находки

| # | Уровень | Где | Проблема и риск | Минимальный фикс |
|---|---|---|---|---|
| 1 | minor | `server/hr/stages.py:89-92` | Уникальность названия этапа без учёта регистра проверяется только кодом. При гонке — 500 или два «одинаковых» этапа | Уникальный индекс по `lower(title)` следующей миграцией или перехват `UniqueViolation` в 409 |
| 2 | minor | `server/hr/api/stages.py:60-103,151-156` | Нет ограничений длины для `title`/`label`/`note`/`comment` и числа `items` | `Field(max_length=…)` |
| 3 | minor | `server/hr/api/stages.py:159-177` | Любой рекрутер ставит итоги в чужой вакансии и меняет чужой отчёт; след есть только в `decided_by` | Решение человека: ограничить или записать в спеку (review.md, находка 8) |
