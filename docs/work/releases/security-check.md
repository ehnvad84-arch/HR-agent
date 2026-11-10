# Security-check: releases

**Вердикт:** pass. Пунктов fail нет; мелочи перечислены в «Находках».

Применимые фреймворки по `docs/rules/security.md`: OWASP Top 10, OWASP LLM Top 10, OWASP Agentic AI Threats,
MITRE ATLAS, NIST AI RMF. Фича не затрагивает модель, промпты и данные модели, поэтому разделы LLM, ATLAS и NIST
в основном n/a. Пункты, которые касаются агента-разработчика (теги ставит агент), разобраны в Agentic.

## Базовый минимум

- [x] В диффе и истории ветки нет секретов. Поиск по `git diff main...HEAD` на шаблоны ключей, паролей и токенов ничего не нашёл; `update.conf.example` без секретов, пароль базы — в `~/.pgpass`.
- [x] `.env` не в индексе (`git ls-files --error-unmatch server/.env` → не найден). `.gitignore` дополнен (`.venv`, `.venvs/`, `.releases/`, `update.conf`); `.cursorignore` и deny в `.claude/settings.json` содержат `.env`.
- [x] Доступа к продакшну нет: ни прод-ключей, ни прод-адресов. Скрипты запускает инженер на сервере, тег ставит агент на GitHub. Это по спеке и AD-8; в `security.md` п.1 не нарушен.
- [x] Права минимальные: deploy key только на чтение, ed25519, 600, `IdentitiesOnly`, `StrictHostKeyChecking yes` (`ОБНОВЛЕНИЕ_портала.md` §2.1). `update.conf` с правом записи для группы или других — отказ, выход 2 (`update.sh:43`, `rollback.sh:34`; в `db_copy.sh` такой проверки нет — М2). Копия базы: `umask 077`, файл 600; каталог 700 не навязывается существующему — М3.
- [x] Сканеры запущены (ниже).

Дополнительно по правилам проекта:

- `set -x` в скриптах нет. `env-missing` печатает только имена. В `ops.release.note` и `job_run.error` попадают только служебные тексты и пути, значений там нет.
- `.env` подгружается в окружение вызовов `hr.cli`, значения не печатаются. В `db_copy.sh` весь `.env` уходит в окружение `eval "$PG_DUMP"` — М1.
- Тег-аргумент проверяется `^v[0-9]+\.[0-9]+\.[0-9]+$` до любого использования во всех пяти скриптах; везде в кавычках. Тест `test_bad_tag_exit_2` проверяет `v1.1.0;id` и `--force`.
- Удаления по путям (`rm -rf "$WT"`, `.venvs/<тег>`) строятся только из проверенного тега. `compat_check.sh` удаляет базу, только если имя оканчивается на `_compat_test` (проверка в двух местах).
- `ops.change_default`: plpgsql без динамического SQL, параметры типизированы.

## Сканеры

| Инструмент | Команда | Результат |
|---|---|---|
| Snyk — зависимости (SCA) | `snyk test` | не запускался: Snyk не установлен, по `security.md` п.5 заменён на pip-audit и Dependabot |
| Snyk — код (SAST) | `snyk code test` | не запускался: Snyk не установлен |
| pip-audit | `qa-test .venv/bin/pip-audit -r requirements.txt` | `No known vulnerabilities found` (`requirements.txt` в диффе не менялся) |
| ruff | `.venv/bin/ruff check .` | `All checks passed!` |
| shellcheck | — | не установлен, не запускался; скрипты прочитаны вручную |
| agentshield | `agentshield scan --path .` | не требовался: дифф не трогает `.claude/`, хуки, MCP, скиллы и плагины |
| Claude Code Security / `/security-review` | запускает главная сессия | — |

## OWASP Top 10 (2021)

| Пункт | Результат | Где |
|---|---|---|
| A01 Broken Access Control | pass | `GET /api/version` без входа отдаёт ровно `{version, date}` (`hr/api/version.py`, тест `test_api_version_without_login`); `base.read` не нужен, AD-7 не затронут. Остальное — CLI на сервере. |
| A02 Cryptographic Failures | pass | GitHub по SSH с закреплённым ключом хоста. Копия базы с ПДн лежит без шифрования, но с правами 600 и на сервере заказчика: решение спеки, мелочь М3 про права каталога. |
| A03 Injection | pass | SQL только с `%s`; тег — по шаблону; `eval` только для команд `update.conf`, а этот файл — код установки с проверкой прав. JSON для `job-record --stats` собирается из чисел и проверенного тега. |
| A04 Insecure Design | pass | Проверки идут до остановки, копия базы — до миграций, возврат кода при провале, совместимость N-1. Восстановление после прерывания — важное В1 в review.md (надёжность, не безопасность). |
| A05 Security Misconfiguration | pass | `/api/version` раскрывает номер версии — так и задумано в спеке, доступно только через туннель (AD-6). Мелочи М2 и М3. |
| A06 Vulnerable and Outdated Components | pass | pip-audit чистый; на сервере ставятся закреплённые версии `requirements.txt`. |
| A07 Identification and Authentication Failures | n/a | Вход не менялся. |
| A08 Software and Data Integrity Failures | pass (принятый риск) | `update.sh` исполняет скрипт из тега (`update.sh:31-37`). Тег приходит по SSH с закреплённым ключом хоста и deploy key, `git fetch` без `--force` не даёт переписать локальный тег, коммит печатается и попадает в `ops.release.git_commit`. Теги не подписаны, а ставит их агент с правом push. Взлом этой учётной записи означает исполнение кода на сервере заказчика. Риск принят в спеке («Решения»), но в `security.md` → «Принятые риски» не записан (С1). |
| A09 Security Logging and Monitoring Failures | pass | `ops.release` (update, auto_rollback, rollback, baseline, actor, commit), `ops.job_run` для `db_copy`. |
| A10 SSRF | n/a | `CHECK_URL` задаёт инженер в `update.conf`, пользовательского ввода нет. |

## OWASP LLM Top 10 (2025)

| Пункт | Результат | Где |
|---|---|---|
| LLM01 Prompt Injection | n/a | Фича модель не вызывает. |
| LLM02 Sensitive Information Disclosure | n/a | Данные в модель не передаются. Копия базы с ПДн — см. A02. |
| LLM03 Supply Chain | n/a | Модели и их зависимости не менялись. |
| LLM04 Data and Model Poisoning | n/a | — |
| LLM05 Improper Output Handling | n/a | — |
| LLM06 Excessive Agency | n/a | — |
| LLM07 System Prompt Leakage | n/a | — |
| LLM08 Vector and Embedding Weaknesses | n/a | — |
| LLM09 Misinformation | n/a | — |
| LLM10 Unbounded Consumption | n/a | — |

## OWASP Agentic AI Threats

Агент в продукте фичей не меняется. Пункты оценены для процесса выпуска: тег ставит агент-разработчик, `update.sh`
исполняет код тега на сервере заказчика.

| Пункт | Результат | Где |
|---|---|---|
| T1 Memory Poisoning | n/a | — |
| T2 Tool Misuse | pass | Скрипты агента (`release.sh`, `compat_check.sh`) работают только с `*_test` и `*_compat_test`; доступа к серверу заказчика у агента нет. |
| T3 Privilege Compromise | pass (принятый риск) | Право push тегов у агента — решение человека от 10.11 (спека, «Решения»), см. A08 и С1. |
| T4 Resource Overload | pass | pytest в `release.sh` и `compat_check.sh` идёт через `qa-test`. |
| T5 Cascading Hallucination Attacks | n/a | — |
| T6 Intent Breaking & Goal Manipulation | n/a | — |
| T7 Misaligned & Deceptive Behaviors | pass | Выпуск проходит Гейт 3 и мерж человеком; тег только на `main` после мержа. |
| T8 Repudiation & Untraceability | pass | `ops.release.git_commit`, `actor`; коммит тега печатается при обновлении. |
| T9 Identity Spoofing & Impersonation | n/a | — |
| T10 Overwhelming Human in the Loop | n/a | — |
| T11 Unexpected RCE and Code Attacks | pass (принятый риск) | Исполнение скрипта из тега — см. A08. `update.conf` — код установки с проверкой прав; в `db_copy.sh` её нет (М2). |
| T12 Agent Communication Poisoning | n/a | — |
| T13 Rogue Agents in Multi-Agent Systems | n/a | — |
| T14 Human Attacks on Multi-Agent Systems | n/a | — |
| T15 Human Manipulation | n/a | — |

## MITRE ATLAS / NIST AI RMF

n/a: фича не касается ML и LLM компонентов, так и записано в спеке («MITRE ATLAS / LLM … неприменимо»).

## Находки

| # | Уровень | Где | Проблема и риск | Минимальный фикс |
|---|---|---|---|---|
| С1 | мелочь | `docs/rules/security.md` → «Принятые риски» | Два решения записаны только в таблице спеки: неподписанные теги, которые ставит агент с правом push, и `update.sh`, исполняющий скрипт тега на сервере заказчика. Это граница доверия между агентом и продом (п.1), её стоит держать в правилах проекта. | Одна строка с датой 2026-11-10 в «Принятых рисках». |
| М1 | мелочь | `server/scripts/db_copy.sh:39-44` | Весь `.env` (ключи hh, `SECRETS_KEY`, LLM) уходит в окружение `eval "$PG_DUMP"` и `$PG_RESTORE`. Минимальные права нарушены; `.env` исполняется bash-ем. | Грузить `.env` только в подоболочке `hr_cli()`. |
| М2 | мелочь | `server/scripts/db_copy.sh:30-32` | Права `update.conf` не проверяются, когда `db_copy.sh` вызывает таймер. | Проверка `& 022`, как в `update.sh:43`. |
| М3 | мелочь | `server/scripts/db_copy.sh:51` | Уже существующему каталогу копии права 700 не навязываются. | `chmod 700 "$BACKUP_DIR"`. |
| М7 | мелочь | `ОБНОВЛЕНИЕ_портала.md:142-143` | Предсказуемый `/tmp/ruware-update.sh` в команде первого запуска. | `mktemp` или файл в `~`. |
