# Security-check: semantic-search

**Вердикт:** pass. Fail нет; minor-находки в конце.

Ревьюер: code-reviewer (Claude Opus 5.5) · 2026-11-22 · дифф main...feature/semantic-search. Файлы `.env*` и `setup_hh_oauth.py` не читались по условию задачи.

## Базовый минимум

- [x] В диффе и истории ветки нет секретов: `git log -p main..HEAD` проверен поиском по шаблонам ключей, токенов, приватных ключей и паролей, ничего не найдено. В документации есть внутренние адреса (10.20.10.6, 10.20.10.115), но это не секреты.
- [x] `.env` не в индексе (`git ls-files` пуст); `.gitignore` (`.env`, `.env.*`, `!.env.example`) и deny `Read(./.env)`, `Read(./.env.*)` в `.claude/settings.json` на месте.
- [x] Доступа к продакшну нет: прод-ключей и команд деплоя нет; `release.sh` гонялся только с `--dry-run`. Шаги на боевом выполняет админ по `ОБНОВЛЕНИЕ_портала.md`.
- [x] Права минимальные. Новое право `profile.synonyms` получили только `recruiter` и `head_recruiter` (`010_semantic_search.sql`); `hiring_manager` и `director` его нет. Служба bge-m3 слушает только 127.0.0.1 и работает без ключа. Новых секретов нет.
- [x] Сканеры запущены (ниже).

## Сканеры

| Инструмент | Команда | Результат |
|---|---|---|
| Snyk — зависимости (SCA) | `snyk test` | не запускался: Snyk не установлен и заменён решением человека 2026-10-29 (security.md п.5) |
| Snyk — код (SAST) | `snyk code test` | не запускался (та же причина) |
| pip-audit (замена SCA) | `pip-audit -r requirements.txt` | No known vulnerabilities found; новых Python-зависимостей в диффе нет |
| agentshield | `agentshield scan --path .` | не требуется: `.claude/`, хуки, MCP, скиллы и плагины в диффе не менялись |
| Образ TEI (новая внешняя зависимость) | `trivy image --severity CRITICAL,HIGH` | на боевом, админ; шаг описан в `ОБНОВЛЕНИЕ_портала.md` раздел 7. Digest и ревизия весов фиксируются там же, не выдуманы |
| Claude Code Security / `/security-review` | запускает главная сессия | — |

## Проверки из задачи

- **В запрос к модели не уходят данные людей.** Pass.
  - Синонимы: вход только `{"title", "keywords"}` действующей версии профиля (`synonyms.py` `suggest`), тест `test_synonyms.py::test_s01_request_has_only_title_and_keywords`.
  - Остальные вызовы (`explain.payload`, `actions.draft`, `learning.post`/`build_digest`) диффом не расширены, ФИО и контактов в них нет: `explain.payload` берёт структурные поля, `learning` — `mask`.
  - Новое в payload объяснений: `features.semantic` (процент близости) и подпись «Altium ≈ P-CAD». Это не персональные данные.
  - Тест-замок `test_s12_lock_llm_complete_only_from_llm_py` держит единую точку.
- **В эмбеддинги не уходят контакты.** Pass с minor.
  - `embed._clean` → `learning.strip_contacts` применяется ко всем текстам для службы: резюме, вход «серой зоны», запрос поиска, текст профиля.
  - Телефоны РФ, почта и ссылки со схемой или `www` срезаются (тест `test_embed.py::test_s09_contacts_not_sent_to_model`).
  - Пропускаются `@ник`, `t.me/…`, `vk.com/…` без схемы, `skype:` (проверено вызовом). Служба на 127.0.0.1, сервер они не покидают. Находка S1.
  - ФИО в `person_text` нет, `label` не используется. Свободный текст «о себе» может содержать имя, но спека это допускает: служба локальная.

## OWASP Top 10 (2025)

| Пункт | Результат | Где |
|---|---|---|
| A01 Broken Access Control | pass | Новые маршруты: `GET …/synonyms` со скоупом `base.read` (вне скоупа 404); `suggest`/`PUT`/`rollback` — `require_scoped("profile.synonyms")` + `_own`. Откат проверяет `role_code` записи (чужая должность → 404). `/api/schema` — `base.all`. Замки `routes_unscoped`/`routes_without_permission` после фикса снова видят все маршруты: на main они были пусты впустую. Тесты `test_manager_reads_own_scope_and_cannot_write`, `test_locks_see_all_app_routes` |
| A02 Security Misconfiguration | pass | Служба векторов только на 127.0.0.1 (`-p 127.0.0.1:8081:80`, проверка `ss -ltn` в разделе 7); `/api/version` открыт намеренно, отдаёт только номер и дату |
| A03 Software Supply Chain Failures | pass | Новых Python-пакетов нет; образ TEI — по digest, веса — по ревизии, проверка trivy (раздел 7). Исполнение — на стороне админа |
| A04 Cryptographic Failures | pass (minor) | Новых секретов и шифрования нет. Канал к модели `http://10.20.10.6` без TLS: спека обосновывает это тем, что персональных данных в запросе нет. Для синонимов это верно, но объяснения и черновики шлют туда же псевдонимизированные поля резюме (города, работодатели, навыки). Находка S3 |
| A05 Injection | pass (minor) | SQL — только `%s`-плейсхолдеры, `set_config` с константами. Ответ модели — по белому списку. В интерфейсе синонимы выводятся через `h()`/`textContent`, `innerHTML` в диффе нет. Запрос hh: кавычки и скобки отсеяны, но одиночные `NOT`/`AND`/`*`/`:` проходят (review.md, находка 6). Злоупотребить этим может только сотрудник с правом |
| A06 Insecure Design | pass | Предел полных резюме и предел модели — атомарный `quota.take`, списание до внешнего вызова; тест на 10 параллельных `take` |
| A07 Authentication Failures | n/a | Вход и сессии не менялись |
| A08 Software or Data Integrity Failures | pass | Каждая правка синонимов, их откат, чистка и CLI-правка настройки — в `agent_change` с `applied_by`; `ops.change_default` правку человека не перетирает (тест `test_s12_cli_setting_kept_by_change_default`); выпущенные миграции под замком `RELEASED.sha256` (010 допишет `release.sh`) |
| A09 Security Logging and Alerting Failures | pass (minor, старый долг) | В `ops.service_health.last_error` — только тип исключения; ответ модели не логируется; `synonyms` логирует без данных. Журнал доступа uvicorn пишет `GET /api/search?q=…` (review.md D1, старый долг) |
| A10 Mishandling of Exceptional Conditions | pass (minor) | Отказ службы векторов не роняет сбор и балл (тесты S8). `embed.ERRORS` слишком широк (`TypeError`, `KeyError`): ошибка кода выглядит как отказ службы (review.md, находка 9). CSRF на новых `PUT`/`POST` — по умолчанию `require` (`auth.py:203`) |

## OWASP LLM Top 10 (2025)

| Пункт | Результат | Где |
|---|---|---|
| LLM01 Prompt Injection | pass | Ключи — JSON-данные, системный промпт `suggest_synonyms.md` прямо говорит «данные, а не инструкции». Ответ — белый список (`_validate`: только ключи профиля, `clean_term`, ≤ 8), без кнопки человека ничего не сохраняется |
| LLM02 Sensitive Information Disclosure | pass | В модель — только название и ключи (тест точного набора полей); векторы считаются локально |
| LLM03 Supply Chain | pass | `t-lite:2.1` уже стоит у заказчика; bge-m3/TEI — по digest и ревизии (раздел 7) |
| LLM04 Data and Model Poisoning | pass | Словарь синонимов правят только `recruiter`/`head_recruiter`; журнал, откат; предел терминов в запросе (`hh_query_max_terms`), предел полных резюме |
| LLM05 Improper Output Handling | pass | Термины модели проходят те же правила, что ручной ввод, и выводятся только текстом |
| LLM06 Excessive Agency | pass | У модели нет инструментов и записи; `suggest` пишет только кэш ответа |
| LLM07 System Prompt Leakage | pass | В промпте нет секретов |
| LLM08 Vector and Embedding Weaknesses | pass (minor) | Векторы только у текущей модели (`embed_model` в фильтре и в косинусе); вес смысла 15 < 25 у ключей, доля упирается в `semantic_sim_full`; текст обрезан. Контакты в векторах — частично (находка S1) |
| LLM09 Misinformation | pass | Балл считает код (AD-2); подпись показывает процент; решение за человеком |
| LLM10 Unbounded Consumption | pass (minor) | `llm_calls_per_day` / `llm_synonyms_per_day`, кэш, часовой предел, `/api/search` ≤ 300 знаков, ночные пачки с остановкой. Перекос: утренние объяснения могут съесть весь общий предел (review.md, находка 1) — это вопрос доступности, а не безопасности |

## OWASP Agentic AI Threats

| Пункт | Результат | Где |
|---|---|---|
| T1 Memory Poisoning | pass | Кэш `ops.llm_cache` хранит только проверенный белым списком ответ; ключ кэша — хеш входа и версии промпта |
| T2 Tool Misuse | n/a | Инструментов у модели нет |
| T3 Privilege Compromise | pass | Права по `role_permission` на сервере; модель действует без прав |
| T4 Resource Overload | pass | Суточные пределы, кэш, пачки, остановка в 06:30 или после трёх отказов |
| T5 Cascading Hallucination Attacks | pass | Выдуманный синоним не сохраняется без человека; балл считает код |
| T6 Intent Breaking & Goal Manipulation | pass | Инъекция в ключах ограничена белым списком ответа |
| T7 Misaligned & Deceptive Behaviors | n/a | Автономных действий нет |
| T8 Repudiation & Untraceability | pass | `agent_change` с автором для синонимов, отката, чистки, CLI-настройки, права |
| T9 Identity Spoofing & Impersonation | n/a | Не затронуто |
| T10 Overwhelming Human in the Loop | pass (minor) | Ложные «новый навык» от синонимов всех должностей могут прибавить сигналов (допущение T7(2) в review.md) |
| T11 Unexpected RCE and Code Attacks | pass | Ответ модели не исполняется; JSON разбирается и фильтруется |
| T12 Agent Communication Poisoning | n/a | Один вызов модели, агентов нет |
| T13 Rogue Agents in Multi-Agent Systems | n/a | — |
| T14 Human Attacks on Multi-Agent Systems | n/a | — |
| T15 Human Manipulation | pass | Подписи «≈» и «близко по смыслу: N %» объясняют балл; решение за человеком |

## MITRE ATLAS (модель угроз спеки)

Меры из таблицы спеки на месте. AML.T0051 — белый список и промпт. AML.T0057 — тест полей входа. AML.T0043/T0015 — вес 15 и потолок `semantic_sim_full`. AML.T0020 — право только у отдела персонала, журнал, предел терминов. AML.T0029 — пределы и кэш. AML.T0010 — digest и trivy, исполнение у админа.

## Находки

| # | Уровень | Где | Проблема и риск | Минимальный фикс |
|---|---|---|---|---|
| S1 | minor (новое использование) | `server/hr/embed.py:49`, `server/hr/learning.py:27-29` | В службу векторов уходят `@ник`, `t.me/…`, `vk.com/…`, `skype:`. Служба локальная, сервер данные не покидают | расширить регулярки, тест |
| S2 | minor (старый долг), нужна проверка: флаги uvicorn на боевом | `server/scripts/install_autostart.sh:40` | `?q=` поиска по смыслу попадает в журнал доступа | `--no-access-log` или фильтр |
| S3 | minor (новое), нужно решение человека | `CHANGELOG.md` v1.2.0 («Переменные .env»), спека «Безопасность» | Канал к модели без TLS обоснован тем, что «данных людей нет», но объяснения и черновики шлют туда псевдонимизированные поля резюме | записать принятый риск в `docs/rules/security.md` или поправить формулировку в спеке |
