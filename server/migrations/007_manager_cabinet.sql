-- Кабинет руководителя (manager-cabinet): выгрузка сотрудников 1С, ручные роли, одноразовые коды входа,
-- передачи кандидатов в отдел персонала, личные поправки и «мой балл». Только добавляет; новое — в ops.

CREATE TABLE IF NOT EXISTS ops.user_sync (
    id           bigserial PRIMARY KEY,
    file_name    text NOT NULL,
    sha256       text NOT NULL UNIQUE,              -- тот же файл дважды не применяется
    exported_at  timestamptz NOT NULL,
    received_at  timestamptz NOT NULL DEFAULT now(),
    state        text NOT NULL,                     -- applied | rejected
    users        integer,
    changes      integer NOT NULL DEFAULT 0,
    error        text
);

-- Роль, поправленная человеком на экране «Доступ»: выгрузка 1С её не перетирает
CREATE TABLE IF NOT EXISTS ops.user_override (
    user_id  integer PRIMARY KEY REFERENCES app_user(id) ON DELETE CASCADE,
    role     user_role NOT NULL,
    set_by   integer REFERENCES app_user(id),
    set_at   timestamptz NOT NULL DEFAULT now(),
    note     text
);

-- Одноразовый код первого входа: в базе только HMAC кода; живой код у человека один
CREATE TABLE IF NOT EXISTS ops.invite_code (
    id            bigserial PRIMARY KEY,
    user_id       integer NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
    code_hash     text NOT NULL,
    issued_by     integer REFERENCES app_user(id),
    issued_at     timestamptz NOT NULL DEFAULT now(),
    expires_at    timestamptz NOT NULL,
    used_at       timestamptz,
    revoked_at    timestamptz,
    failed_count  smallint NOT NULL DEFAULT 0,
    locked_until  timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS invite_code_live_idx ON ops.invite_code (user_id)
    WHERE used_at IS NULL AND revoked_at IS NULL;

-- Передача руководителем понравившегося человека в отдел персонала (S9–S11)
CREATE TABLE IF NOT EXISTS ops.handoff (
    id             bigserial PRIMARY KEY,
    person_id      bigint NOT NULL REFERENCES person(id) ON DELETE CASCADE,
    vacancy_id     bigint NOT NULL REFERENCES vacancy(id) ON DELETE CASCADE,
    from_user      integer NOT NULL REFERENCES app_user(id),
    comment        text NOT NULL,
    state          text NOT NULL DEFAULT 'sent',    -- sent | taken | rejected
    created_at     timestamptz NOT NULL DEFAULT now(),
    decided_by     integer REFERENCES app_user(id),
    decided_at     timestamptz,
    reject_reason  text,
    candidate_id   bigint REFERENCES candidate(id) ON DELETE SET NULL,
    UNIQUE (person_id, vacancy_id)
);
CREATE INDEX IF NOT EXISTS handoff_vacancy_idx ON ops.handoff (vacancy_id, state);
CREATE INDEX IF NOT EXISTS handoff_from_idx ON ops.handoff (from_user, created_at DESC);

-- Личные поправки к баллу (S14–S16): действуют только для автора
CREATE TABLE IF NOT EXISTS ops.user_tweak (
    id           bigserial PRIMARY KEY,
    user_id      integer NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
    role_code    text,                              -- пусто — на все должности
    kind         text NOT NULL,                     -- plus | minus | stop | weight
    value        jsonb NOT NULL,                    -- {"text": …} или {"feature": …, "weight": …}
    state        text NOT NULL DEFAULT 'active',    -- proposed | active | off
    source       text NOT NULL DEFAULT 'manual',    -- manual | agent
    feedback_id  bigint REFERENCES feedback_message(id) ON DELETE SET NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    decided_at   timestamptz
);
CREATE INDEX IF NOT EXISTS user_tweak_user_idx ON ops.user_tweak (user_id, state);

-- «Мой балл»: кэш балла с личными поправками, пересчитывается задачей user_rescore
CREATE TABLE IF NOT EXISTS ops.user_match (
    user_id          integer NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
    person_id        bigint NOT NULL REFERENCES person(id) ON DELETE CASCADE,
    role_profile_id  integer NOT NULL REFERENCES role_profile(id) ON DELETE CASCADE,
    score            smallint NOT NULL,
    features         jsonb NOT NULL DEFAULT '{}',
    scored_at        timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, person_id, role_profile_id)
);
CREATE INDEX IF NOT EXISTS user_match_rank_idx ON ops.user_match (user_id, role_profile_id, score DESC);

CREATE INDEX IF NOT EXISTS staff_position_unit_idx ON staff_position (org_unit_id);
CREATE INDEX IF NOT EXISTS app_user_unit_idx ON app_user (org_unit_id);
CREATE INDEX IF NOT EXISTS vacancy_recruiter_idx ON vacancy (recruiter_id);
CREATE INDEX IF NOT EXISTS vacancy_manager_idx ON vacancy (hiring_manager_id);

INSERT INTO settings (key, value) VALUES
  ('handoff_comment_min', '50'),
  ('invite_code_hours',   '72'),
  ('shared_tweak_users',  '3'),
  ('users_max_drop_pct',  '20'),
  ('manager_title_words', '["руководитель", "начальник", "директор"]'),
  ('hr_org_unit_title',   '"Отдел управления персоналом"'),
  ('my_score_min_base',   '30')
ON CONFLICT (key) DO NOTHING;

-- ------------------------------------------------------------- права (матрица спеки)

INSERT INTO agent_change (kind, before, after, note)
SELECT 'permission', NULL,
       '{"add": ["recruiter:base.all", "recruiter:collect.run", "recruiter:handoff.review", "recruiter:invite.issue",
                 "recruiter:digest.read", "recruiter:tweak.own", "recruiter:feedback.write",
                 "head_recruiter:base.all", "head_recruiter:collect.run", "head_recruiter:handoff.review",
                 "head_recruiter:invite.issue", "head_recruiter:digest.read", "head_recruiter:handoff.all",
                 "head_recruiter:tweak.own",
                 "hiring_manager:profile.edit", "hiring_manager:feedback.write", "hiring_manager:tweak.own",
                 "hiring_manager:handoff.create",
                 "director:profile.edit", "director:feedback.write", "director:tweak.own", "director:handoff.create"]}'::jsonb,
       'миграция 007_manager_cabinet: кабинет руководителя, права по ролям'
WHERE NOT EXISTS (SELECT 1 FROM role_permission WHERE permission = 'handoff.create');

INSERT INTO role_permission (role, permission) VALUES
  ('recruiter', 'base.all'), ('recruiter', 'collect.run'), ('recruiter', 'handoff.review'),
  ('recruiter', 'invite.issue'), ('recruiter', 'digest.read'), ('recruiter', 'tweak.own'),
  ('recruiter', 'feedback.write'),
  ('head_recruiter', 'base.all'), ('head_recruiter', 'collect.run'), ('head_recruiter', 'handoff.review'),
  ('head_recruiter', 'invite.issue'), ('head_recruiter', 'digest.read'), ('head_recruiter', 'handoff.all'),
  ('head_recruiter', 'tweak.own'),
  ('hiring_manager', 'profile.edit'), ('hiring_manager', 'feedback.write'), ('hiring_manager', 'tweak.own'),
  ('hiring_manager', 'handoff.create'),
  ('director', 'profile.edit'), ('director', 'feedback.write'), ('director', 'tweak.own'),
  ('director', 'handoff.create')
ON CONFLICT DO NOTHING;
