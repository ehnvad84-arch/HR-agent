-- 012 (funnel-report): этапы вакансии из одной библиотеки, наборы с утверждением, отметки «прошёл / не прошёл /
-- отказался» по паре кандидат×вакансия, паузы вакансии. Воронку считает код по этим отметкам (AD-2), модель не
-- участвует. candidate.status остаётся и пишется кодом как производный — v1.2.0 работает на этой базе (AD-8).
-- Только добавляет; уникальность — ограничениями в CREATE TABLE.

-- 8 строк отчёта отдела персонала, всегда одни и те же (их Excel)
CREATE TABLE IF NOT EXISTS ops.funnel_row (
    code  text PRIMARY KEY,
    ord   smallint NOT NULL UNIQUE,
    title text NOT NULL
);
INSERT INTO ops.funnel_row (code, ord, title) VALUES
  ('selected',  1, 'Отобранные резюме'),
  ('contact',   2, 'Первичный контакт'),
  ('screening', 3, 'Пройденное первичное интервью'),
  ('interview', 4, 'Интервью'),
  ('meeting',   5, 'Собеседование'),
  ('security',  6, 'Проверка СБ'),
  ('offer',     7, 'Оффер'),
  ('first_day', 8, 'Пришедшие в 1 день')
ON CONFLICT (code) DO NOTHING;

-- Причины исхода этапа (S7). outcome: failed — отказали мы, refused — отказался сам; пусто — у обоих исходов
-- («Другое»). only_row — причина только на этой строке отчёта.
CREATE TABLE IF NOT EXISTS ops.fail_reason (
    code          text PRIMARY KEY,
    title         text NOT NULL,
    outcome       text CHECK (outcome IN ('failed', 'refused')),
    only_row      text REFERENCES ops.funnel_row(code),
    needs_comment boolean NOT NULL DEFAULT false,
    ord           smallint NOT NULL UNIQUE
);
INSERT INTO ops.fail_reason (code, title, outcome, only_row, needs_comment, ord) VALUES
  ('skills',         'Опыт или навыки',            'failed',  NULL,    false, 1),
  ('test_task',      'Тестовое задание',           'failed',  NULL,    false, 2),
  ('security',       'Проверка СБ',                'failed',  NULL,    false, 3),
  ('conditions',     'Не подходят условия труда',  'refused', NULL,    false, 4),
  ('offer_declined', 'Отказ от оффера',            'refused', 'offer', false, 5),
  ('no_contact',     'Не вышел на связь',          'refused', NULL,    false, 6),
  ('no_show',        'Не пришёл на встречу',       'refused', NULL,    false, 7),
  ('no_first_day',   'Не вышел в первый день',     'refused', NULL,    false, 8),
  ('other',          'Другое',                     NULL,      NULL,    true,  9)
ON CONFLICT (code) DO NOTHING;

-- Библиотека этапов (S1): каждый этап — в одной строке отчёта; строку не меняют, этап выводят из работы
CREATE TABLE IF NOT EXISTS ops.stage (
    id          serial PRIMARY KEY,
    title       text NOT NULL UNIQUE,
    report_row  text NOT NULL REFERENCES ops.funnel_row(code),
    is_standard boolean NOT NULL DEFAULT false,
    is_active   boolean NOT NULL DEFAULT true,
    created_by  integer REFERENCES app_user(id),
    created_at  timestamptz NOT NULL DEFAULT now()
);
INSERT INTO ops.stage (title, report_row, is_standard)
SELECT title, code, true FROM ops.funnel_row ORDER BY ord
ON CONFLICT (title) DO NOTHING;
INSERT INTO ops.stage (title, report_row) VALUES
  ('Тестовое задание',              'interview'),
  ('Оплачиваемое тестовое',         'interview'),
  ('Собеседование с руководителем', 'meeting')
ON CONFLICT (title) DO NOTHING;

-- Набор этапов вакансии (S2–S5): рекрутер предлагает, руководитель ОП утверждает; действует последняя approved.
-- На утверждении у вакансии не больше одного набора.
CREATE TABLE IF NOT EXISTS ops.stage_set (
    id               bigserial PRIMARY KEY,
    vacancy_id       bigint NOT NULL REFERENCES vacancy(id) ON DELETE CASCADE,
    version          integer NOT NULL,
    state            text NOT NULL CHECK (state IN ('proposed', 'approved', 'declined', 'withdrawn')),
    note             text,
    proposed_by      integer REFERENCES app_user(id),
    proposed_at      timestamptz NOT NULL DEFAULT now(),
    decided_by       integer REFERENCES app_user(id),
    decided_at       timestamptz,
    decision_comment text,
    UNIQUE (vacancy_id, version),
    EXCLUDE USING btree (vacancy_id WITH =) WHERE (state = 'proposed')
);

-- Этапы набора по порядку. slot — постоянный номер этапа вакансии через версии набора: по нему отметки кандидатов
-- остаются на своём этапе, когда набор меняется
CREATE TABLE IF NOT EXISTS ops.stage_set_item (
    set_id   bigint NOT NULL REFERENCES ops.stage_set(id) ON DELETE CASCADE,
    pos      smallint NOT NULL,
    slot     integer NOT NULL,
    stage_id integer NOT NULL REFERENCES ops.stage(id),
    label    text,                                   -- «Собеседование с главным инженером»
    PRIMARY KEY (set_id, pos),
    UNIQUE (set_id, slot)
);

-- Отметки этапов пары кандидат×вакансия (S6–S10, S15, S16). report_row — снимок строки при входе.
-- Исход пишется один раз; ошибочную отметку отменяют пометкой cancelled_*, строка не удаляется и в счёт не входит.
CREATE TABLE IF NOT EXISTS ops.candidate_stage (
    id             bigserial PRIMARY KEY,
    candidate_id   bigint NOT NULL REFERENCES candidate(id) ON DELETE CASCADE,
    vacancy_id     bigint NOT NULL REFERENCES vacancy(id) ON DELETE CASCADE,
    slot           integer NOT NULL,
    stage_id       integer NOT NULL REFERENCES ops.stage(id),
    report_row     text NOT NULL REFERENCES ops.funnel_row(code),
    entered_by     integer REFERENCES app_user(id),
    entered_at     timestamptz NOT NULL DEFAULT now(),
    carried        boolean NOT NULL DEFAULT false,   -- перенесён при выпуске v1.3.0 без истории (S15)
    outcome        text CHECK (outcome IN ('passed', 'failed', 'refused')),
    reason         text REFERENCES ops.fail_reason(code),
    comment        text,
    decided_by     integer REFERENCES app_user(id),
    decided_at     timestamptz,
    cancelled_by   integer REFERENCES app_user(id),
    cancelled_at   timestamptz,
    cancel_comment text,
    CHECK (outcome IS NULL OR outcome = 'passed' OR reason IS NOT NULL),
    EXCLUDE USING btree (candidate_id WITH =, vacancy_id WITH =, slot WITH =) WHERE (cancelled_at IS NULL),
    EXCLUDE USING btree (candidate_id WITH =, vacancy_id WITH =) WHERE (cancelled_at IS NULL AND outcome IS NULL)
);
CREATE INDEX IF NOT EXISTS candidate_stage_vacancy_idx ON ops.candidate_stage (vacancy_id, report_row)
    WHERE cancelled_at IS NULL;

-- Паузы вакансии (S14): дни паузы и дни между закрытием и повторным открытием не входят в срок ведения
CREATE TABLE IF NOT EXISTS ops.vacancy_pause (
    id         bigserial PRIMARY KEY,
    vacancy_id bigint NOT NULL REFERENCES vacancy(id) ON DELETE CASCADE,
    from_date  date NOT NULL,
    to_date    date,
    kind       text NOT NULL CHECK (kind IN ('pause', 'closed_gap')),
    by         integer REFERENCES app_user(id),
    comment    text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (to_date IS NULL OR to_date >= from_date),
    EXCLUDE USING btree (vacancy_id WITH =) WHERE (to_date IS NULL)
);

-- ------------------------------------------------------------- права

-- Утверждать наборы этапов и править библиотеку — руководитель ОП (решение 23.11)
INSERT INTO agent_change (kind, before, after, note)
SELECT 'permission', NULL, '{"add": ["head_recruiter:stage.approve"]}'::jsonb,
       'миграция 012_funnel_stages: утверждение наборов этапов вакансии'
WHERE NOT EXISTS (SELECT 1 FROM role_permission WHERE permission = 'stage.approve');

INSERT INTO role_permission (role, permission) VALUES ('head_recruiter', 'stage.approve')
ON CONFLICT DO NOTHING;

-- ------------------------------------------------------------- настройки (AD-8: через ops.change_default)

SELECT ops.change_default('012', 'stage_fail_other_min', NULL, '100',
                          'знаков в комментарии к причине «Другое» у исхода этапа');
