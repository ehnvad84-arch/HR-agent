-- Умный поиск (semantic-search): синонимы ключей, векторы профилей, суточные пределы hh и модели, здоровье служб.
-- Только добавляет (AD-8): прошлая версия новых таблиц не читает, ключ «semantic» в весах отбрасывает.

-- Синонимы ключей профиля (S1–S6). По коду должности, а не по версии: правка действует сразу, без утверждения,
-- и переживает новые версии профиля. История и откат — в agent_change (kind «synonyms»).
CREATE TABLE IF NOT EXISTS role_synonym (
    role_code  text NOT NULL,
    keyword    text NOT NULL,                      -- нормализованный ключ профиля
    synonyms   text[] NOT NULL DEFAULT '{}',
    updated_by integer REFERENCES app_user(id),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (role_code, keyword)
);

-- Вектор профиля (S7–S10): текст — название, ключи, синонимы, must_have и nice_to_have; пересчёт при смене text_sha256.
CREATE TABLE IF NOT EXISTS ops.profile_embedding (
    role_profile_id integer PRIMARY KEY REFERENCES role_profile(id) ON DELETE CASCADE,
    embed_model     text NOT NULL,
    text_sha256     text NOT NULL,
    embedding       vector(1024) NOT NULL,
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- Суточные пределы (S11, S12): день по Москве, ключ — hh_full_resume, llm_call, llm_synonyms.
CREATE TABLE IF NOT EXISTS ops.daily_quota (
    day  date NOT NULL,
    key  text NOT NULL,
    used integer NOT NULL DEFAULT 0,
    PRIMARY KEY (day, key)
);

-- Здоровье внешних служб (S8): down_since — с первого отказа до первого успеха; last_error — только тип исключения.
CREATE TABLE IF NOT EXISTS ops.service_health (
    service     text PRIMARY KEY,
    down_since  timestamptz,
    last_ok_at  timestamptz,
    last_error  text
);

-- ------------------------------------------------------------- права

INSERT INTO agent_change (kind, before, after, note)
SELECT 'permission', NULL,
       '{"add": ["recruiter:profile.synonyms", "head_recruiter:profile.synonyms"]}'::jsonb,
       'миграция 010_semantic_search: синонимы ключей правят рекрутеры; руководители только видят'
WHERE NOT EXISTS (SELECT 1 FROM role_permission WHERE permission = 'profile.synonyms');

INSERT INTO role_permission (role, permission) VALUES
  ('recruiter', 'profile.synonyms'), ('head_recruiter', 'profile.synonyms')
ON CONFLICT DO NOTHING;

-- ------------------------------------------------------------- настройки (AD-8: через ops.change_default)

SELECT ops.change_default('010', 'hh_full_resume_per_day', NULL, '300', 'полных резюме hh в сутки на портал');
SELECT ops.change_default('010', 'llm_calls_per_day', NULL, '500', 'вызовов модели в сутки, кроме синонимов');
SELECT ops.change_default('010', 'llm_synonyms_per_day', NULL, '100', 'предложений синонимов моделью в сутки');
SELECT ops.change_default('010', 'llm_assist_enabled', NULL, 'true',
                          'объяснения, черновики и разбор замечаний моделью');
SELECT ops.change_default('010', 'semantic_weight_default', NULL, '15', 'вес «Близости по смыслу»');
SELECT ops.change_default('010', 'semantic_sim_floor', NULL, '0.40', 'косинус, ниже которого смысл не засчитан');
SELECT ops.change_default('010', 'semantic_sim_full', NULL, '0.75', 'косинус полного балла за смысл');
SELECT ops.change_default('010', 'hh_query_max_terms', NULL, '20', 'терминов в запросе к hh');
SELECT ops.change_default('010', 'embed_batch_size', NULL, '32', 'текстов в одном запросе к bge-m3');
SELECT ops.change_default('010', 'embed_night_until', NULL, '"06:30"', 'конец ночного пересчёта векторов, Москва');
