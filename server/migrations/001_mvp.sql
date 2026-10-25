-- =====================================================================
-- Дополнения к schema_recruiting.sql, без которых MVP не работает.
-- Только добавляет: ни одна таблица и ни одно поле основной схемы не меняется.
-- Применяется после основной схемы: python -m hr.cli migrate
-- =====================================================================

CREATE TABLE IF NOT EXISTS schema_migration (
    name       text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);

-- Вход по варианту 2 раздела 10 ТЗ: логин из 1С, пароль задаётся при первом входе.
-- При едином входе (OIDC) поле остаётся пустым.
ALTER TABLE app_user ADD COLUMN IF NOT EXISTS password_hash text;
ALTER TABLE app_user ADD COLUMN IF NOT EXISTS password_set_at timestamptz;

-- Промпты с версиями: текст меняется без выкладки кода (раздел 6 ТЗ).
CREATE TABLE IF NOT EXISTS agent_prompt (
    id         serial PRIMARY KEY,
    code       text NOT NULL,              -- explain_score | first_message | draft_extract | ...
    version    integer NOT NULL,
    body       text NOT NULL,
    is_active  boolean NOT NULL DEFAULT true,
    created_by integer REFERENCES app_user(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (code, version)
);

-- Каждый запуск фоновой задачи: по этой таблице watchdog понимает, прошёл ли
-- утренний обход, а инженер видит, что упало и почему.
CREATE TABLE IF NOT EXISTS job_run (
    id          bigserial PRIMARY KEY,
    job         text NOT NULL,
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    ok          boolean,
    attempt     smallint NOT NULL DEFAULT 1,
    stats       jsonb NOT NULL DEFAULT '{}',
    error       text
);
CREATE INDEX IF NOT EXISTS job_run_job_idx ON job_run (job, started_at DESC);

-- Под какую вакансию ведётся кандидат: по этой ссылке строится воронка.
ALTER TABLE candidate ADD COLUMN IF NOT EXISTS vacancy_id bigint REFERENCES vacancy(id) ON DELETE SET NULL;
-- «Отложить» возвращает карточку через postpone_months
ALTER TABLE person ADD COLUMN IF NOT EXISTS postponed_until date;
-- Ник из резюме или профилей: по нему ищутся следы в сети
ALTER TABLE person ADD COLUMN IF NOT EXISTS nicknames text[] NOT NULL DEFAULT '{}';

-- Балл на момент решения: правило «прошлый отказ, балл вырос на 10» (раздел 3.3 архитектуры)
ALTER TABLE decision_log ADD COLUMN IF NOT EXISTS score_at smallint;
ALTER TABLE decision_log ADD COLUMN IF NOT EXISTS role_profile_id integer REFERENCES role_profile(id);

-- Номера черновиков «Ч-<год>-<порядковый>»
CREATE SEQUENCE IF NOT EXISTS request_draft_number_seq;

-- Состояние диалога с ботом: что бот спросил и чего ждёт в ответ
CREATE TABLE IF NOT EXISTS bot_dialog (
    telegram_id bigint PRIMARY KEY,
    draft_id    bigint REFERENCES request_draft(id) ON DELETE SET NULL,
    awaiting    text,                       -- dislike_reason | criteria_confirm | close_comment ...
    context     jsonb NOT NULL DEFAULT '{}',
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- Файлы обмена с 1С: что уже принято, чтобы не применять дважды
CREATE TABLE IF NOT EXISTS exchange_file (
    id          bigserial PRIMARY KEY,
    kind        text NOT NULL,              -- users | requests | orgstructure
    file_name   text NOT NULL,
    sha256      text NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    state       text NOT NULL,              -- applied | rejected | skipped
    stats       jsonb NOT NULL DEFAULT '{}',
    error       text,
    UNIQUE (kind, sha256)
);

-- Подтверждение правки критериев черновика: бот предложил, руководитель решает
CREATE TABLE IF NOT EXISTS draft_criteria_proposal (
    id          bigserial PRIMARY KEY,
    draft_id    bigint NOT NULL REFERENCES request_draft(id) ON DELETE CASCADE,
    proposal    jsonb NOT NULL,             -- {"add_must": [...], "add_stop": [...], "note": "..."}
    text        text NOT NULL,
    state       text NOT NULL DEFAULT 'pending',   -- pending | accepted | declined
    created_at  timestamptz NOT NULL DEFAULT now(),
    decided_at  timestamptz
);

INSERT INTO settings (key, value) VALUES
  ('postpone_months',          '3'),
  ('vacancy_starving_days',    '5'),
  ('score_grew_after_months',  '6'),
  ('stale_observation_days',   '45'),
  ('crawl_max_pages',          '2000'),
  ('crawl_per_profile_pages',  '5'),
  ('hh_area',                  '53'),     -- Краснодар в справочнике hh
  ('hh_area_relocation',       '113'),    -- вся Россия
  ('trudvsem_region',          '"2300000000000"'),
  ('merge_auto_similarity',    '0.92'),
  ('merge_ask_similarity',     '0.6'),
  ('social_rate_per_sec',      '1'),
  ('digest_recipients_role',   '"head_recruiter"'),
  ('watchdog_morning_deadline','"07:30"'),
  ('feedback_group_min',       '2')
ON CONFLICT (key) DO NOTHING;

-- Права, которых не было в основной схеме: кто меняет статус кандидата, кто
-- ведёт вакансии, кто смотрит журнал доступа. Матрицу читает и API, и интерфейс.
INSERT INTO role_permission (role, permission) VALUES
  ('recruiter', 'vacancy.edit'), ('head_recruiter', 'vacancy.edit'),
  ('recruiter', 'candidate.status'), ('head_recruiter', 'candidate.status'), ('hiring_manager', 'candidate.status'),
  ('recruiter', 'action.decide'), ('head_recruiter', 'action.decide'),
  ('recruiter', 'contact.open'), ('head_recruiter', 'contact.open'),
  ('recruiter', 'social.confirm'), ('head_recruiter', 'social.confirm'),
  ('recruiter', 'feedback.write'), ('head_recruiter', 'feedback.write'),
  ('recruiter', 'report.read'), ('director', 'dossier.export'),
  ('head_recruiter', 'audit.read'), ('director', 'audit.read'),
  ('recruiter', 'feedback.read'), ('head_recruiter', 'feedback.read'), ('director', 'feedback.read')
ON CONFLICT DO NOTHING;

-- Агент — тоже пользователь: от его имени пишутся статусы и предложения
INSERT INTO app_user (login, full_name, role, external_id)
VALUES ('agent', 'Агент подбора', 'agent', 'system:agent')
ON CONFLICT (login) DO NOTHING;
