# GigaChat API

Сверено с OpenAPI https://developers.sber.ru/docs/files/openapi/gigachat/api.yml 2026-10-29. Код: `server/hr/gigachat.py`, вызов через `llm.complete`.

- **Токен:** `POST https://ngw.devices.sberbank.ru:9443/api/v2/oauth`.
  - Заголовки: `Authorization: Basic <ключ авторизации из личного кабинета>`, `RqUID: <uuid4>`. Тело формой: `scope=GIGACHAT_API_PERS`. Для физлица это бесплатный пакет; `GIGACHAT_API_B2B` и `GIGACHAT_API_CORP` — для юрлиц.
  - Ответ: `{access_token, expires_at}`, токен живёт 30 минут. `expires_at` в примере дан в секундах, в старых ответах был в миллисекундах — код понимает оба варианта.
  - Не чаще 10 запросов в секунду.
- **Генерация:** `POST https://api.giga.chat/v1/chat/completions`, `Authorization: Bearer <токен>`.
  - Формат OpenAI: тело `{model, messages, temperature, max_tokens}`, ответ `choices[0].message.content`, `usage`.
  - Структурный вывод — `response_format: {type: "json_schema", ...}`. Код его не шлёт и просит JSON в промпте.
  - Ошибки: 401 — токен; 404 — неверная модель; 422 — параметры или длина; 429 — частота; 500.
- **Модели:** `GigaChat` и `GigaChat-2` (Lite, по умолчанию `GIGACHAT_MODEL`), `GigaChat-2-Pro`, `GigaChat-2-Max`, `GigaChat-3-Ultra`.
- **Пакет физлица** (на 2026-10): 365 млн токенов на 12 месяцев, из них 250 млн на Lite. Генерация в один поток. Остаток — `GET /balance` (только для пакетов).
- **TLS:** корневой сертификат НУЦ Минцифры. Путь к файлу — `GIGACHAT_CA_FILE`, он добавляется к системным, проверка не отключается.
- **Переменные `.env`:** `GIGACHAT_AUTH_KEY` (секрет), `GIGACHAT_SCOPE`, `GIGACHAT_MODEL`, `GIGACHAT_CA_FILE`, `GIGACHAT_OAUTH_URL`, `GIGACHAT_BASE_URL`.
