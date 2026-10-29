-- =====================================================================
-- Фича workplace: дополнения к схеме. Только добавляет.
-- Всё новое — в схеме ops: в public остаются 46 таблиц и 4 представления (пункт 2 приёмки).
-- =====================================================================

-- ------------------------------------------------------------- вход

-- Пароль временной учётной записи до входа из 1С (фича exchange-1c). В app_user пароля нет.
CREATE TABLE IF NOT EXISTS ops.user_password (
    user_id      integer PRIMARY KEY REFERENCES app_user(id) ON DELETE CASCADE,
    hash         text NOT NULL,                   -- bcrypt
    changed_at   timestamptz NOT NULL DEFAULT now(),
    failed_count smallint NOT NULL DEFAULT 0,
    locked_until timestamptz
);

-- Сессия браузера: в cookie случайный токен с подписью, в базе только хеши
CREATE TABLE IF NOT EXISTS ops.web_session (
    token_hash   text PRIMARY KEY,
    user_id      integer NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
    csrf_hash    text NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    expires_at   timestamptz NOT NULL,
    revoked_at   timestamptz
);
CREATE INDEX IF NOT EXISTS web_session_user_idx ON ops.web_session (user_id);

-- ------------------------------------------------------------- данные экранов

-- Фото из резюме: скачано один раз и сжато; уходит вместе с наблюдением
CREATE TABLE IF NOT EXISTS ops.person_photo (
    observation_id bigint PRIMARY KEY REFERENCES observation(id) ON DELETE CASCADE,
    data           bytea CHECK (data IS NULL OR octet_length(data) <= 10240),
    sha256         text,
    state          text NOT NULL CHECK (state IN ('ok', 'absent', 'failed')),
    attempts       smallint NOT NULL DEFAULT 0,
    fetched_at     timestamptz NOT NULL DEFAULT now(),
    CHECK (state <> 'ok' OR data IS NOT NULL)
);

-- Ответы модели по ключу входа: повторное открытие досье модель не зовёт
CREATE TABLE IF NOT EXISTS ops.llm_cache (
    key        text PRIMARY KEY,                  -- sha256(назначение, модель, версия промпта, вход)
    purpose    text NOT NULL,                     -- explain | draft | chat | digest
    person_id  bigint REFERENCES person(id) ON DELETE CASCADE,
    reply      jsonb NOT NULL,
    tokens     integer,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS llm_cache_person_idx ON ops.llm_cache (person_id);

-- Ответ агента в чате обучения: короче 40 знаков бывает, поэтому не в feedback_message
CREATE TABLE IF NOT EXISTS ops.feedback_reply (
    feedback_id bigint PRIMARY KEY REFERENCES feedback_message(id) ON DELETE CASCADE,
    body        text NOT NULL,
    by          text NOT NULL CHECK (by IN ('model', 'template')),
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- Кандидат в воронке вакансии; кандидат без строки здесь — в резерве без вакансии
CREATE TABLE IF NOT EXISTS ops.candidate_vacancy (
    candidate_id bigint NOT NULL REFERENCES candidate(id) ON DELETE CASCADE,
    vacancy_id   bigint NOT NULL REFERENCES vacancy(id) ON DELETE CASCADE,
    added_by     integer REFERENCES app_user(id),
    added_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (candidate_id, vacancy_id)
);
CREATE INDEX IF NOT EXISTS candidate_vacancy_vacancy_idx ON ops.candidate_vacancy (vacancy_id);

-- «Не наш» по профилю убирает человека из выдачи только по этому профилю
CREATE INDEX IF NOT EXISTS decision_log_reject_idx ON decision_log (role_profile_id, person_id)
    WHERE kind = 'reject';

-- ------------------------------------------------------------- права

-- Открытие контакта и вакансии — у рекрутеров; чат обучения — и у руководителя подбора,
-- единственного пользователя до exchange-1c
INSERT INTO agent_change (kind, before, after, note)
SELECT 'permission', NULL,
       '{"add": ["recruiter:contact.open", "recruiter:vacancy.edit", "head_recruiter:contact.open",
                 "head_recruiter:vacancy.edit", "head_recruiter:feedback.write"]}'::jsonb,
       'миграция 002_workplace'
WHERE NOT EXISTS (SELECT 1 FROM role_permission WHERE role = 'head_recruiter' AND permission = 'contact.open');

INSERT INTO role_permission (role, permission) VALUES
  ('recruiter', 'contact.open'), ('recruiter', 'vacancy.edit'),
  ('head_recruiter', 'contact.open'), ('head_recruiter', 'vacancy.edit'), ('head_recruiter', 'feedback.write')
ON CONFLICT DO NOTHING;

-- ------------------------------------------------------------- настройки

INSERT INTO settings (key, value) VALUES
  ('session_idle_hours',  '12'),
  ('session_max_days',    '7'),
  ('login_max_failures',  '5'),
  ('login_lock_minutes',  '15'),
  ('photo_max_bytes',     '10240'),
  ('preview_max_persons', '5000'),
  ('llm_pause_minutes',   '60'),
  ('llm_calls_per_hour',  '60'),
  ('base_page_size',      '50')
ON CONFLICT (key) DO NOTHING;
