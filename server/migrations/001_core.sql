-- =====================================================================
-- Фича core: дополнения к schema_recruiting.sql. Только добавляет.
-- Служебное — в схеме ops, чтобы в public оставались 46 таблиц и 4 представления
-- (пункт 2 приёмки ТЗ). Применяется командой: python -m hr.cli migrate
-- =====================================================================

-- ------------------------------------------------------------- ops

-- Каждый запуск фоновой задачи: по этому журналу watchdog понимает, прошёл ли обход
CREATE TABLE IF NOT EXISTS ops.job_run (
    id          bigserial PRIMARY KEY,
    job         text NOT NULL,
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    ok          boolean,
    attempt     smallint NOT NULL DEFAULT 1,
    stats       jsonb NOT NULL DEFAULT '{}',
    error       text
);
CREATE INDEX IF NOT EXISTS job_run_job_idx ON ops.job_run (job, started_at DESC);

-- Токены внешних сервисов, зашифрованы ключом SECRETS_KEY. В settings их нет.
CREATE TABLE IF NOT EXISTS ops.oauth_token (
    provider          text PRIMARY KEY,           -- hh
    access_token_enc  bytea NOT NULL,
    refresh_token_enc bytea,
    expires_at        timestamptz NOT NULL,
    account_id        text,                       -- работодатель, под которым выдан токен
    updated_at        timestamptz NOT NULL DEFAULT now()
);

-- Одноразовый state для OAuth: в базе только хеш
CREATE TABLE IF NOT EXISTS ops.oauth_state (
    state_hash  text PRIMARY KEY,
    provider    text NOT NULL,
    expires_at  timestamptz NOT NULL,
    used_at     timestamptz
);

-- Принятые файлы выгрузок из кабинетов: один и тот же файл дважды не принимается
CREATE TABLE IF NOT EXISTS ops.import_file (
    id          bigserial PRIMARY KEY,
    file_name   text NOT NULL,
    sha256      text NOT NULL UNIQUE,
    source_code text NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    state       text NOT NULL,                    -- applied | rejected
    stats       jsonb NOT NULL DEFAULT '{}',
    error       text
);

-- ------------------------------------------------------------- поля

-- Ник из резюме и профилей: по нему сшиваются площадки и ищутся следы в сети
ALTER TABLE person ADD COLUMN IF NOT EXISTS nicknames text[] NOT NULL DEFAULT '{}';
CREATE INDEX IF NOT EXISTS person_nicknames_idx ON person USING gin (nicknames);
-- «Отложить» до даты: сигналов до неё нет (пишет фича workplace)
ALTER TABLE person ADD COLUMN IF NOT EXISTS postponed_until date;
-- Балл на момент отказа: правило «был отказ, балл вырос» (архитектура, 3.3)
ALTER TABLE decision_log ADD COLUMN IF NOT EXISTS score_at smallint;
ALTER TABLE decision_log ADD COLUMN IF NOT EXISTS role_profile_id integer REFERENCES role_profile(id);
-- Каким способом построен вектор: векторы разных способов несравнимы
ALTER TABLE person_embedding ADD COLUMN IF NOT EXISTS embed_model text NOT NULL DEFAULT 'hash-v1';
-- Быстрая проверка, есть ли уже открытый сигнал по человеку
CREATE INDEX IF NOT EXISTS alert_open_idx ON alert (person_id, role_profile_id) WHERE state = 'new';

-- ------------------------------------------------------------- источники

INSERT INTO source (code, title, kind, mode, base_url, is_active, cost_note, legal_note) VALUES
  ('hh', 'hh.ru', 'jobboard', 'api', 'https://api.hh.ru', true,
   'подписка работодателя на базу резюме', 'официальное API работодателя'),
  ('trudvsem', 'Работа России', 'gov', 'api', 'https://opendata.trudvsem.ru', false,
   'бесплатно', 'открытые данные; включается после проверки эндпойнта резюме'),
  ('habr', 'Хабр Карьера', 'it_community', 'cabinet_export', 'https://career.habr.com', true,
   'суточный доступ', 'выгрузка из кабинета компании'),
  ('avito', 'Авито Работа', 'classified', 'cabinet_export', 'https://www.avito.ru', false,
   'пакет просмотров', 'выгрузка из кабинета до одобрения API'),
  ('manual', 'Ручной ввод', 'manual', 'manual', NULL, true, NULL, 'внёс рекрутер')
ON CONFLICT (code) DO NOTHING;

-- ------------------------------------------------------------- настройки

INSERT INTO settings (key, value) VALUES
  ('score_grew_after_months',   '6'),
  ('stale_observation_days',    '45'),
  ('crawl_max_pages',           '2000'),
  ('crawl_per_profile_pages',   '5'),
  ('hh_area',                   '53'),       -- Краснодар в справочнике hh
  ('hh_area_relocation',        '113'),      -- вся Россия
  ('hh_full_resume_per_run',    '200'),      -- экономный режим: полное резюме только прошедшим порог (S32)
  ('hh_recheck_per_run',        '100'),      -- перепроверка «пропал ли» по id (S11)
  ('trudvsem_region',           '"2300000000000"'),
  ('merge_auto_similarity',     '0.92'),
  ('merge_ask_similarity',      '0.6'),
  ('social_rate_per_sec',       '1'),
  ('stop_factor_cap',           '40'),
  ('must_penalty_pct',          '10'),
  ('must_penalty_max_pct',      '30'),
  ('donor_alert_departments',   '["sales", "rnd_hw", "rnd_sw"]'),
  ('local_area_cities',         '["краснодар", "адыгейск", "яблоновский", "энем", "горячий ключ", "динская", "северская", "афипский", "елизаветинская", "новая адыгея"]'),
  ('llm_explain_per_run',       '500'),
  ('social_max_persons_per_run','500'),
  ('import_max_file_mb',        '20'),
  ('import_max_rows',           '10000')
ON CONFLICT (key) DO NOTHING;
