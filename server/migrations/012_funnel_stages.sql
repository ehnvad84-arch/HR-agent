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

-- ------------------------------------------------------------- перенос (S15)

-- Учёт с чистого листа: вакансиям open|on_hold без набора — утверждённый v1 из 8 стандартных этапов; открытым парам
-- кандидат×вакансия без отметок — отметка carried на этапе строки по статусу кандидата. Кандидат в нескольких
-- вакансиях: статус — в вакансию с последней связью, в остальные открытые — «Отобранные резюме». Строки нет в наборе —
-- ближайший этап до неё. new, reserve и закрытые статусы не переносятся. Повтор ничего не меняет.
-- Вызывают эта миграция и python -m hr.cli funnel-carry (возврат на v1.3.0 после отката).
CREATE OR REPLACE FUNCTION ops.funnel_carry() RETURNS TABLE (sets integer, marks integer)
LANGUAGE plpgsql AS $$
DECLARE
    n_sets  integer;
    n_marks integer;
BEGIN
    WITH todo AS (
        SELECT v.id FROM vacancy v
        WHERE v.state IN ('open', 'on_hold')
          AND NOT EXISTS (SELECT 1 FROM ops.stage_set ss WHERE ss.vacancy_id = v.id)
        ORDER BY v.id
    ), new_set AS (
        INSERT INTO ops.stage_set (vacancy_id, version, state, note, decided_at)
        SELECT id, 1, 'approved', 'стандартный набор при переходе на v1.3.0', now() FROM todo
        RETURNING id
    ), new_item AS (
        INSERT INTO ops.stage_set_item (set_id, pos, slot, stage_id)
        SELECT ns.id, r.ord, r.ord, st.id
        FROM new_set ns CROSS JOIN ops.funnel_row r
        JOIN ops.stage st ON st.report_row = r.code AND st.is_standard
        RETURNING 1
    )                                                    -- изменяющие CTE выполняются целиком и без ссылки на них
    SELECT count(*) INTO n_sets FROM new_set;

    WITH status_row (status, code) AS (VALUES
        ('picked_by_agent', 'selected'), ('first_message_sent', 'contact'), ('replied', 'contact'),
        ('phone_interview', 'screening'), ('manager_interview', 'meeting'), ('test_task', 'meeting'),
        ('security_check', 'security'), ('offer', 'offer')
    ), latest AS (
        SELECT DISTINCT ON (candidate_id) candidate_id, vacancy_id
        FROM ops.candidate_vacancy ORDER BY candidate_id, added_at DESC, vacancy_id DESC
    ), pair AS (
        SELECT cv.candidate_id, cv.vacancy_id,
               CASE WHEN l.vacancy_id = cv.vacancy_id THEN sr.code ELSE 'selected' END AS want
        FROM ops.candidate_vacancy cv
        JOIN candidate c ON c.id = cv.candidate_id
        JOIN status_row sr ON sr.status = c.status::text
        JOIN vacancy v ON v.id = cv.vacancy_id AND v.state IN ('open', 'on_hold')
        JOIN latest l ON l.candidate_id = cv.candidate_id
        WHERE NOT EXISTS (SELECT 1 FROM ops.candidate_stage cs
                          WHERE cs.candidate_id = cv.candidate_id AND cs.vacancy_id = cv.vacancy_id
                            AND cs.cancelled_at IS NULL)
    ), cur_set AS (
        SELECT DISTINCT ON (vacancy_id) vacancy_id, id
        FROM ops.stage_set WHERE state = 'approved' ORDER BY vacancy_id, version DESC
    ), target AS (
        SELECT DISTINCT ON (p.candidate_id, p.vacancy_id)
               p.candidate_id, p.vacancy_id, i.slot, i.stage_id, st.report_row
        FROM pair p
        JOIN cur_set s ON s.vacancy_id = p.vacancy_id
        JOIN ops.stage_set_item i ON i.set_id = s.id
        JOIN ops.stage st ON st.id = i.stage_id
        JOIN ops.funnel_row have ON have.code = st.report_row
        JOIN ops.funnel_row want ON want.code = p.want
        WHERE have.ord <= want.ord
        ORDER BY p.candidate_id, p.vacancy_id, have.ord DESC, i.pos
    ), new_mark AS (
        INSERT INTO ops.candidate_stage (candidate_id, vacancy_id, slot, stage_id, report_row, carried)
        SELECT candidate_id, vacancy_id, slot, stage_id, report_row, true FROM target
        RETURNING 1
    )
    SELECT count(*) INTO n_marks FROM new_mark;

    RETURN QUERY SELECT n_sets, n_marks;
END;
$$;

SELECT ops.funnel_carry();

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
