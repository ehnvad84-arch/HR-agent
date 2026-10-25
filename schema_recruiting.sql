-- =====================================================================
-- База агента подбора персонала «Рувер»
-- PostgreSQL 15+, расширения vector (pgvector) и pg_trgm
-- Применение:  psql -U ruware -d ruware_hr -f schema_recruiting.sql
--
-- Две зоны хранения, см. раздел 1.2 документа «Агент подбора:
-- архитектура, рабочее место, база»:
--   зона наблюдения — публичные профили с площадок, БЕЗ контактов и ФИО;
--   зона кандидата  — персональные данные людей, с которыми начали
--                     работать, всегда с записанным основанием обработки.
-- Наполнение базы (решение 26.09.2026): агент сам заводит всех, кто набрал балл
-- не ниже watch_threshold хотя бы по одной из 83 должностей, и следит за ними
-- годами; рекрутер поднимает человека в 'watched' или помечает 'dropped'.
-- Вакансии приходят тремя путями: рекрутер заводит сам, заявка руководителя
-- после согласования, публикация на hh подхватывается сама (vacancy.origin).
-- Оргструктура и штатное расписание приходят из 1С:ERP выгрузкой и только
-- читаются (org_unit, staff_position); здесь ведётся лишь сопоставление с профилями.
-- Телеграм-бот руководителей готовит черновик заявки (request_draft); сама заявка
-- заводится и согласуется в 1С и связывается с черновиком по его номеру.
--
-- Граница проходит по таблицам: observation/person — наблюдение,
-- candidate/contact/resume_file — кандидат. Контакт не попадает в зону
-- наблюдения ни при каких условиях: это то, что отделяет выборку по
-- площадкам от картотеки людей.
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ---------------------------------------------------------------- типы

CREATE TYPE source_kind AS ENUM (
  'jobboard',      -- hh, Работа.ру, Зарплата.ру, SuperJob, Карьерист
  'gov',           -- Работа России (trudvsem)
  'classified',    -- Авито Работа
  'it_community',  -- Хабр Карьера, GitHub
  'messenger',     -- телеграм-каналы и чаты
  'referral',      -- рекомендация сотрудника
  'manual'         -- ручной ввод рекрутером
);

-- Как забираем данные. Определяет и правовой режим: api и cabinet_export
-- разрешены площадкой, scrape — нет, поэтому значения scrape здесь нет.
CREATE TYPE fetch_mode AS ENUM ('api', 'cabinet_export', 'manual');

-- Радиус поиска, разный у линейного персонала, продаж и управленцев
CREATE TYPE geo_scope AS ENUM ('krasnodar', 'remote_ru', 'relocation_ru');

-- События по резюме — то, ради чего база наблюдает за людьми во времени
CREATE TYPE obs_event_kind AS ENUM (
  'new',                -- появился в поле зрения
  'resume_updated',     -- обновил резюме
  'employer_changed',   -- сменил работодателя
  'salary_changed',     -- изменил зарплатные ожидания
  'opened_for_search',  -- открыл резюме для поиска
  'skill_added',        -- добавил навык или сертификат из нашего списка
  'geo_changed',        -- переехал или заявил готовность к переезду
  'disappeared'         -- резюме скрыто или удалено
);

-- Пятнадцать статусов из пункта 6.2 опросника
CREATE TYPE candidate_status AS ENUM (
  'new', 'picked_by_agent', 'first_message_sent', 'replied', 'refused_contact',
  'phone_interview', 'manager_interview', 'test_task', 'security_check',
  'offer', 'hired', 'no_show', 'reserve', 'rejected_by_us', 'rejected_by_candidate'
);

-- Основание обработки персональных данных. Пишется в момент, когда
-- человек переходит из наблюдения в кандидаты, и не может быть пустым.
CREATE TYPE consent_basis AS ENUM (
  'consent_written',      -- письменное согласие
  'consent_electronic',   -- согласие при отклике или в переписке
  'candidate_initiated',  -- человек сам прислал резюме
  'contract',             -- трудовой договор заключён
  'not_obtained'          -- основания нет: работа с карточкой блокируется
);

CREATE TYPE action_kind AS ENUM (
  'first_message', 'follow_up', 'invite_interview', 'send_test_task',
  'reject_notice', 'move_to_reserve', 'merge_persons', 'open_contact'
);

CREATE TYPE action_state AS ENUM ('proposed', 'approved', 'declined', 'executed', 'expired');

CREATE TYPE alert_reason AS ENUM (
  'new_high_score',     -- новый человек с баллом выше порога
  'event_on_pool',      -- событие по резюме человека из резерва
  'score_grew',         -- прошлый отказ, балл вырос
  'donor_company',      -- опыт в компании из списка доноров
  'vacancy_starving'    -- вакансия без кандидатов дольше нормы
);

-- Где искали профессиональные следы человека помимо резюме
CREATE TYPE social_kind AS ENUM (
  'github', 'gitlab', 'telegram', 'vk', 'ok', 'habr', 'linkedin', 'facebook', 'instagram',
  'x', 'youtube', 'dzen', 'tiktok', 'forum', 'conference', 'other');

-- Совпадение профиля с человеком: вероятное подтверждает рекрутер.
-- Однофамилец в досье хуже пустого поля, поэтому 'probable' фактом не считается.
CREATE TYPE link_confidence AS ENUM ('confirmed', 'probable', 'rejected');

-- Режим наблюдения. Решение 26.09.2026: агент сам заводит всех, кто набрал балл
-- не ниже порога watch_threshold, рекрутер может поднять человека в 'watched' —
-- тогда порог для него не действует и уведомления приходят на любое изменение.
CREATE TYPE watch_mode AS ENUM ('observed', 'watched', 'dropped');

-- Откуда в системе появилась вакансия
CREATE TYPE vacancy_origin AS ENUM ('recruiter', 'request_1c', 'hh_publication');

-- Состояние заявки руководителя подразделения на подбор
CREATE TYPE request_state AS ENUM ('draft', 'submitted', 'approved', 'declined', 'cancelled');

-- Контур обучения: что рекрутер пишет агенту в чат
CREATE TYPE feedback_tag AS ENUM ('remark', 'recommendation', 'criticism', 'question',
                                  'reject_reason', 'postpone_reason');

-- Решение руководителя отдела персонала по предложению из недельной сводки
CREATE TYPE digest_decision AS ENUM ('pending', 'accepted', 'declined', 'discuss');

CREATE TYPE user_role AS ENUM ('recruiter', 'head_recruiter', 'hiring_manager', 'director', 'agent');

-- ------------------------------------------------- оргструктура и роли

CREATE TABLE department (
    id          smallserial PRIMARY KEY,
    code        text NOT NULL UNIQUE,     -- sales, rnd_hw, rnd_sw, production, qc, support, it, purchase, finance, hr, marketing
    title       text NOT NULL,
    head_role   text                      -- должность руководителя по оргструктуре
);

-- Профиль роли = должностная инструкция, переведённая в критерии отбора.
-- Версионируется: переоценка людей идёт по действующей версии, а старые
-- оценки остаются привязанными к той версии, по которой их считали.
CREATE TABLE role_profile (
    id               serial PRIMARY KEY,
    department_id    smallint NOT NULL REFERENCES department(id),
    code             text NOT NULL,            -- mop_project, engineer_hw_1, otk_lead, ...
    title            text NOT NULL,            -- как в ДИ
    di_file          text,                     -- имя файла ДИ, откуда собран профиль
    di_storage_key   text,                     -- сам файл в объектном хранилище
    di_revision      text,                     -- редакция инструкции, как она названа в компании
    di_uploaded_at   timestamptz,
    di_uploaded_by   integer,
    version          text NOT NULL,            -- 1.0, 1.1 ...
    valid_from       date NOT NULL DEFAULT current_date,
    is_active        boolean NOT NULL DEFAULT true,
    geo              geo_scope NOT NULL DEFAULT 'krasnodar',
    salary_min       integer,                  -- вилка, ₽ на руки
    salary_max       integer,
    must_have        jsonb NOT NULL DEFAULT '[]',   -- [{"feature":"exp_years","op":">=","value":2}]
    nice_to_have     jsonb NOT NULL DEFAULT '[]',
    stop_factors     jsonb NOT NULL DEFAULT '[]',   -- деловые стоп-факторы, см. 1.3 документа
    weights          jsonb NOT NULL DEFAULT '{}',   -- {"donor_company":20,"exp_years":15,...}
    keywords         text[] NOT NULL DEFAULT '{}',  -- Altium, Embedded, схемотехника, ONVIF
    donor_companies  text[] NOT NULL DEFAULT '{}',  -- Болид, Рубеж, PERCo, ЛУИС+ ...
    approved_by      integer,                       -- app_user.id
    approved_at      timestamptz,
    UNIQUE (code, version)
);
CREATE INDEX role_profile_active_idx ON role_profile (is_active, department_id);

-- ------------------------------------------- оргструктура из 1С:ERP

-- Подразделения и штатное расписание ведутся в 1С:ERP, система их только читает:
-- поправить структуру или число ставок можно только в 1С. Здесь правятся две
-- вещи, которых в 1С нет: к какой функциональной группе (department) относится
-- подразделение и какому профилю роли соответствует должность из 1С.
-- Выгрузка — полный снимок, что изменилось, система вычисляет сама.
-- ФИО сотрудников на позициях в выгрузку не входят: только число занятых ставок.

CREATE TYPE org_change_kind AS ENUM (
  'unit_added', 'unit_renamed', 'unit_moved', 'unit_closed', 'unit_reopened',
  'position_added', 'position_moved', 'position_closed', 'position_reopened',
  'rates_changed',      -- изменилось число ставок по штатному расписанию
  'occupancy_changed'   -- изменилось число занятых ставок
);

-- Один приём выгрузки. Файл применяется целиком или не применяется вовсе.
CREATE TABLE org_sync (
    id           bigserial PRIMARY KEY,
    file_name    text NOT NULL,
    sha256       text NOT NULL UNIQUE,          -- тот же файл дважды не применяется
    exported_at  timestamptz NOT NULL,          -- момент снимка по часам 1С
    received_at  timestamptz NOT NULL DEFAULT now(),
    state        text NOT NULL,                 -- applied | rejected | outdated
    units        integer,
    positions    integer,
    changes      integer NOT NULL DEFAULT 0,
    error        text                           -- почему отклонён
);
CREATE INDEX org_sync_applied_idx ON org_sync (state, exported_at DESC);

CREATE TABLE org_unit (
    id             serial PRIMARY KEY,
    external_id    text NOT NULL UNIQUE,        -- идентификатор подразделения в 1С
    parent_id      integer REFERENCES org_unit(id),
    organization   text,                        -- юрлицо, если их в 1С несколько
    code           text,                        -- код подразделения в 1С
    title          text NOT NULL,
    department_id  smallint REFERENCES department(id),  -- ставит руководитель отдела персонала;
                                                         -- пусто — берётся у родителя
    is_active      boolean NOT NULL DEFAULT true,
    synced_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX org_unit_parent_idx ON org_unit (parent_id);

-- Позиция штатного расписания: подразделение × должность × число ставок.
CREATE TABLE staff_position (
    id               serial PRIMARY KEY,
    external_id      text NOT NULL UNIQUE,      -- идентификатор позиции в 1С
    org_unit_id      integer NOT NULL REFERENCES org_unit(id),
    job_external_id  text NOT NULL,             -- идентификатор должности в 1С
    job_title        text NOT NULL,             -- должность как в 1С
    rates_total      numeric(6,2) NOT NULL CHECK (rates_total >= 0),
    rates_occupied   numeric(6,2) NOT NULL DEFAULT 0 CHECK (rates_occupied >= 0),
    rates_open       numeric(6,2) GENERATED ALWAYS AS (greatest(rates_total - rates_occupied, 0)) STORED,
    valid_from       date,                      -- с какой даты позиция действует в 1С
    is_active        boolean NOT NULL DEFAULT true,
    raw              jsonb NOT NULL DEFAULT '{}',   -- строка выгрузки как есть
    synced_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX staff_position_open_idx ON staff_position (is_active, rates_open);
CREATE INDEX staff_position_job_idx  ON staff_position (job_external_id);

-- Сопоставление должности из 1С с профилем роли. Ведётся по должности, а не по
-- позиции: «Менеджер по продажам» в пяти отделах — одно сопоставление. Ссылка
-- идёт на код профиля, поэтому переживает выход новых версий профиля.
CREATE TABLE job_role_map (
    job_external_id  text PRIMARY KEY,
    job_title        text NOT NULL,
    role_code        text,                      -- role_profile.code; пусто — «не подбираем»
    mapped_by        integer,                   -- app_user.id
    mapped_at        timestamptz NOT NULL DEFAULT now()
);

-- Лента изменений оргструктуры: из неё строятся уведомления и экран истории.
CREATE TABLE org_change (
    id                 bigserial PRIMARY KEY,
    sync_id            bigint NOT NULL REFERENCES org_sync(id),
    kind               org_change_kind NOT NULL,
    org_unit_id        integer REFERENCES org_unit(id),
    staff_position_id  integer REFERENCES staff_position(id),
    before             jsonb,
    after              jsonb,
    created_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX org_change_time_idx ON org_change (created_at DESC);

-- --------------------------------------------------------- источники

CREATE TABLE source (
    id          smallserial PRIMARY KEY,
    code        text NOT NULL UNIQUE,     -- hh, trudvsem, avito, habr, github, tg, referral
    title       text NOT NULL,
    kind        source_kind NOT NULL,
    mode        fetch_mode NOT NULL,
    base_url    text,
    is_active   boolean NOT NULL DEFAULT true,
    cost_note   text,                     -- как платим: подписка, пакет контактов, суточный доступ
    legal_note  text                      -- чем разрешён сбор: API, кабинет, договор
);

-- Очередь обхода: одна строка = один источник × один профиль роли
CREATE TABLE crawl_queue (
    id              bigserial PRIMARY KEY,
    source_id       smallint NOT NULL REFERENCES source(id),
    role_profile_id integer REFERENCES role_profile(id),
    params          jsonb NOT NULL DEFAULT '{}',   -- фильтры запроса к площадке
    state           text NOT NULL DEFAULT 'pending', -- pending | running | done | error
    attempts        smallint NOT NULL DEFAULT 0,
    last_error      text,
    enqueued_at     timestamptz NOT NULL DEFAULT now(),
    finished_at     timestamptz
);
CREATE INDEX crawl_queue_state_idx ON crawl_queue (state, enqueued_at);

-- ------------------------------------------------- ЗОНА НАБЛЮДЕНИЯ

-- Сшитая личность. ФИО и контактов здесь нет: человек опознаётся по
-- набору профилей на площадках. Ярлык нужен, чтобы рекрутер видел в
-- списке не идентификатор, а «Инженер-электронщик, Краснодар».
CREATE TABLE person (
    id            bigserial PRIMARY KEY,
    label         text NOT NULL,
    city          text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    last_event_at timestamptz,
    in_pool       boolean NOT NULL DEFAULT true,   -- держим в резерве и наблюдаем
    watch         watch_mode NOT NULL DEFAULT 'observed',
    watch_by      integer,                         -- кто поднял в 'watched', app_user.id
    watch_at      timestamptz,
    candidate_id  bigint                           -- заполняется при переходе в зону кандидата
);
COMMENT ON COLUMN person.watch IS
  'observed — завёл агент по порогу; watched — рекрутер ведёт особо; dropped — помечен «не наш»';
CREATE INDEX person_pool_idx  ON person (in_pool, last_event_at DESC);
CREATE INDEX person_label_trgm ON person USING gin (label gin_trgm_ops);

-- Публичный профиль на площадке
CREATE TABLE observation (
    id               bigserial PRIMARY KEY,
    source_id        smallint NOT NULL REFERENCES source(id),
    external_id      text NOT NULL,        -- идентификатор резюме на площадке
    url              text,
    person_id        bigint REFERENCES person(id) ON DELETE SET NULL,
    title            text,                 -- желаемая должность
    city             text,
    employer_current text,
    employers        text[] NOT NULL DEFAULT '{}',  -- все места работы: по ним ищем доноров
    skills           text[] NOT NULL DEFAULT '{}',
    exp_years        numeric(4,1),
    avg_tenure_months smallint,            -- средняя длительность места работы
    salary_expect    integer,
    relocation_ready boolean,
    open_for_search  boolean,
    updated_at_src   timestamptz,          -- когда резюме обновлено на площадке
    raw              jsonb NOT NULL,       -- как пришло: чтобы пересчитывать оценку без похода на площадку
    content_hash     text NOT NULL,
    first_seen       timestamptz NOT NULL DEFAULT now(),
    last_seen        timestamptz NOT NULL DEFAULT now(),
    is_gone          boolean NOT NULL DEFAULT false,
    retention_until  date,                 -- пока площадка показывает + 12 месяцев
    UNIQUE (source_id, external_id)
);
CREATE INDEX observation_person_idx ON observation (person_id);
CREATE INDEX observation_city_idx   ON observation (city);
CREATE INDEX observation_seen_idx   ON observation (last_seen DESC);
CREATE INDEX observation_skills_idx ON observation USING gin (skills);
CREATE INDEX observation_empl_idx   ON observation USING gin (employers);

-- История версий: без неё нельзя показать, что именно изменилось
CREATE TABLE obs_version (
    id             bigserial PRIMARY KEY,
    observation_id bigint NOT NULL REFERENCES observation(id) ON DELETE CASCADE,
    content_hash   text NOT NULL,
    raw            jsonb NOT NULL,
    diff           jsonb,                 -- что изменилось против предыдущей версии
    captured_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX obs_version_obs_idx ON obs_version (observation_id, captured_at DESC);

CREATE TABLE obs_event (
    id             bigserial PRIMARY KEY,
    observation_id bigint NOT NULL REFERENCES observation(id) ON DELETE CASCADE,
    person_id      bigint REFERENCES person(id) ON DELETE CASCADE,
    kind           obs_event_kind NOT NULL,
    detail         jsonb NOT NULL DEFAULT '{}',
    happened_at    timestamptz NOT NULL DEFAULT now(),
    processed      boolean NOT NULL DEFAULT false   -- прошло ли через скоринг и правила оповещения
);
CREATE INDEX obs_event_unprocessed_idx ON obs_event (processed, happened_at);
CREATE INDEX obs_event_person_idx      ON obs_event (person_id, happened_at DESC);

-- Спорная сшивка: агент не склеивает молча, а спрашивает рекрутера
CREATE TABLE person_merge_candidate (
    id          bigserial PRIMARY KEY,
    person_a    bigint NOT NULL REFERENCES person(id) ON DELETE CASCADE,
    person_b    bigint NOT NULL REFERENCES person(id) ON DELETE CASCADE,
    similarity  numeric(4,3) NOT NULL,
    evidence    jsonb NOT NULL DEFAULT '{}',
    state       text NOT NULL DEFAULT 'open',   -- open | merged | rejected
    decided_by  integer,
    decided_at  timestamptz,
    CHECK (person_a < person_b)
);

-- Профессиональные следы: GitHub, телеграм-чаты, ВК, Хабр, форумы.
-- Берётся только открытое и только то, что говорит о профессии.
CREATE TABLE social_profile (
    id           bigserial PRIMARY KEY,
    person_id    bigint NOT NULL REFERENCES person(id) ON DELETE CASCADE,
    kind         social_kind NOT NULL,
    url          text NOT NULL,
    handle       text,                          -- ник или идентификатор на площадке
    found_by     text,                          -- по какому варианту имени или признаку нашли
    profile_since date,                         -- с какого времени профиль виден
    activity     text,                          -- что и как часто публикует
    topics       text[] NOT NULL DEFAULT '{}',  -- темы: профессиональные, по ним и оцениваем
    jobseek_signal text,                        -- признаки поиска работы в профиле
    confidence   link_confidence NOT NULL DEFAULT 'probable',
    matched_by   text[] NOT NULL DEFAULT '{}',  -- по чему сошлось: name, employer, city, email, nick
    summary      text,                          -- что видно по профессии: стек, активность, экспертиза
    last_activity date,
    confirmed_by integer,                       -- app_user.id, кто подтвердил вероятное совпадение
    confirmed_at timestamptz,
    first_seen   timestamptz NOT NULL DEFAULT now(),
    last_seen    timestamptz NOT NULL DEFAULT now(),
    raw          jsonb NOT NULL DEFAULT '{}',
    UNIQUE (person_id, url)
);
CREATE INDEX social_person_idx ON social_profile (person_id, confidence);

-- Где искали и не нашли — тоже результат: без этого нельзя отличить
-- «профиля нет» от «не искали». Пишется по каждой площадке из списка поиска.
CREATE TABLE social_search (
    id         bigserial PRIMARY KEY,
    person_id  bigint NOT NULL REFERENCES person(id) ON DELETE CASCADE,
    kind       social_kind NOT NULL,
    queries    text[] NOT NULL DEFAULT '{}',    -- какие варианты имени и ника пробовали
    found      boolean NOT NULL DEFAULT false,
    note       text,                            -- ограничение доступа к площадке и прочее
    checked_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (person_id, kind)
);

-- Варианты написания имени: площадки пишут по-разному, половина профилей заведена латиницей
ALTER TABLE person ADD COLUMN IF NOT EXISTS name_variants text[] NOT NULL DEFAULT '{}';
CREATE INDEX social_kind_idx   ON social_profile (kind, last_activity DESC NULLS LAST);

-- Поиск по смыслу: «схемотехник с Altium из приборостроения»
CREATE TABLE person_embedding (
    person_id   bigint PRIMARY KEY REFERENCES person(id) ON DELETE CASCADE,
    embedding   vector(1024) NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX person_embedding_ann ON person_embedding
    USING hnsw (embedding vector_cosine_ops);

-- ------------------------------------------------- ЗОНА КАНДИДАТА

CREATE TABLE candidate (
    id               bigserial PRIMARY KEY,
    person_id        bigint UNIQUE REFERENCES person(id) ON DELETE SET NULL,
    fio              text NOT NULL,
    birth_date       date,
    city             text,
    current_position text,
    current_employer text,
    relocation_ready boolean,
    status           candidate_status NOT NULL DEFAULT 'new',
    owner_id         integer,             -- ответственный рекрутер, app_user.id
    source_id        smallint REFERENCES source(id),
    resume_url       text,
    first_contact_at timestamptz,
    last_contact_at  timestamptz,
    reject_reason    text,
    retention_until  date NOT NULL,       -- 3 года с последнего контакта, продлевается
    anonymized_at    timestamptz,         -- после этой даты остаются только оценки и воронка
    created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX candidate_status_idx ON candidate (status, owner_id);
CREATE INDEX candidate_fio_trgm   ON candidate USING gin (fio gin_trgm_ops);
CREATE INDEX candidate_retention_idx ON candidate (retention_until);

-- Контакты отдельной таблицей: их удаляют при обезличивании, не трогая
-- историю воронки. hash — для поиска дублей без хранения значения в индексе.
CREATE TABLE contact (
    id           bigserial PRIMARY KEY,
    candidate_id bigint NOT NULL REFERENCES candidate(id) ON DELETE CASCADE,
    kind         text NOT NULL,           -- phone | email | telegram | whatsapp
    value        text NOT NULL,
    value_hash   text NOT NULL,           -- sha256 от нормализованного значения
    is_primary   boolean NOT NULL DEFAULT false,
    verified     boolean NOT NULL DEFAULT false,
    UNIQUE (candidate_id, kind, value_hash)
);
CREATE INDEX contact_hash_idx ON contact (value_hash);

-- Основание обработки. Пустым не бывает: not_obtained — это тоже запись,
-- и при нём интерфейс блокирует переписку и хранение файла резюме.
CREATE TABLE consent (
    id           bigserial PRIMARY KEY,
    candidate_id bigint NOT NULL REFERENCES candidate(id) ON DELETE CASCADE,
    basis        consent_basis NOT NULL,
    obtained_at  timestamptz,
    valid_until  date,
    channel      text,                    -- письмо, форма, отклик, бумага
    doc_url      text,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX consent_candidate_idx ON consent (candidate_id, created_at DESC);

CREATE TABLE resume_file (
    id           bigserial PRIMARY KEY,
    candidate_id bigint NOT NULL REFERENCES candidate(id) ON DELETE CASCADE,
    storage_key  text NOT NULL,           -- ключ в объектном хранилище TimeWeb
    mime         text,
    sha256       text,
    captured_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE status_history (
    id           bigserial PRIMARY KEY,
    candidate_id bigint NOT NULL REFERENCES candidate(id) ON DELETE CASCADE,
    from_status  candidate_status,
    to_status    candidate_status NOT NULL,
    changed_by   integer NOT NULL,        -- app_user.id; агент тоже пользователь
    changed_at   timestamptz NOT NULL DEFAULT now(),
    comment      text
);
CREATE INDEX status_history_cand_idx ON status_history (candidate_id, changed_at DESC);

CREATE TABLE note (
    id           bigserial PRIMARY KEY,
    candidate_id bigint NOT NULL REFERENCES candidate(id) ON DELETE CASCADE,
    author_id    integer NOT NULL,
    body         text NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now()
);

-- ------------------------------------------------------- вакансии

CREATE TABLE vacancy (
    id               bigserial PRIMARY KEY,
    role_profile_id  integer NOT NULL REFERENCES role_profile(id),
    title            text NOT NULL,
    headcount        smallint NOT NULL DEFAULT 1,
    opened_at        date NOT NULL DEFAULT current_date,
    target_date      date,
    hiring_manager_id integer,
    recruiter_id     integer,
    origin           vacancy_origin NOT NULL DEFAULT 'recruiter',
    request_id       bigint,                         -- если пришла заявкой из 1С; ссылка ставится ниже
    staff_position_id integer REFERENCES staff_position(id), -- под какую позицию штатного расписания
    external_id      text,                           -- идентификатор публикации на hh
    close_comment    text,                           -- обязателен при закрытии, см. проверку ниже
    closed_by        integer,                        -- app_user.id
    keep_sourcing    boolean NOT NULL DEFAULT true,  -- закрытие вакансии не останавливает наблюдение
    state            text NOT NULL DEFAULT 'open',   -- open | on_hold | closed | cancelled
    closed_at        date,
    norm_days        smallint NOT NULL DEFAULT 45    -- норма закрытия; сверх неё вакансия «голодает»
);
CREATE INDEX vacancy_state_idx ON vacancy (state, opened_at);

-- Закрыть вакансию можно только с развёрнутым комментарием: короткое «закрыли»
-- ничему не учит ни людей, ни агента. Та же проверка стоит в интерфейсе.
ALTER TABLE vacancy
  ADD CONSTRAINT vacancy_close_comment_ck
  CHECK (state <> 'closed' OR char_length(btrim(coalesce(close_comment, ''))) >= 200);

-- Заявка руководителя подразделения приходит ИЗ 1С выгрузкой: заявки заводятся
-- и согласовываются там, здесь хранится готовая потребность и след согласования.
-- Система заявки не создаёт и не согласовывает — только читает.
CREATE TABLE vacancy_request (
    id              bigserial PRIMARY KEY,
    source_system   text NOT NULL DEFAULT '1С',      -- откуда пришла заявка
    external_id     text NOT NULL,                   -- идентификатор заявки в 1С
    requester_name  text,                            -- кто заказал, как записано в 1С
    department_id   smallint NOT NULL REFERENCES department(id),
    role_profile_id integer REFERENCES role_profile(id),
    title_raw       text,                            -- если такой должности в базе ещё нет
    staff_position_id integer REFERENCES staff_position(id), -- если 1С передала позицию штатного расписания
    headcount       smallint NOT NULL DEFAULT 1,
    reason          text,                            -- рост, замена, проект
    wanted_by       date,
    state           request_state NOT NULL DEFAULT 'approved',
    approved_name   text,                            -- кто согласовал в 1С
    approved_at     timestamptz,
    imported_at     timestamptz NOT NULL DEFAULT now(),
    vacancy_id      bigint REFERENCES vacancy(id) ON DELETE SET NULL,
    raw             jsonb NOT NULL DEFAULT '{}',     -- строка выгрузки как есть
    UNIQUE (source_system, external_id)
);
CREATE INDEX vacancy_request_state_idx ON vacancy_request (state, imported_at);

ALTER TABLE vacancy
  ADD CONSTRAINT vacancy_request_fk
  FOREIGN KEY (request_id) REFERENCES vacancy_request(id) ON DELETE SET NULL;

-- ------------------------------------- черновики заявок из телеграм-бота

-- Решение 24.10.2026: бот руководителей готовит ЧЕРНОВИК заявки, а заявка
-- по-прежнему заводится и согласуется в 1С. Черновик получает номер, номер
-- переносится в заявку 1С, и выгрузка заявок связывает их (vacancy_request.draft_id).
-- Второго контура согласования нет: «утверждён» здесь значит «готов к заведению в 1С».

CREATE TYPE draft_state AS ENUM (
  'draft',       -- руководитель уточняет профиль и смотрит кандидатов
  'submitted',   -- отправлен руководителю отдела персонала на проверку
  'returned',    -- возвращён с комментарием на доработку
  'ready_for_1c',-- проверен, руководитель заводит заявку в 1С с номером черновика
  'linked',      -- заявка с этим номером пришла из 1С
  'cancelled'
);

-- Доступ к боту: руководитель пишет боту, руководитель отдела персонала
-- подтверждает, что это действительно он, и привязывает к сотруднику из 1С.
CREATE TABLE tg_access_request (
    id            bigserial PRIMARY KEY,
    telegram_id   bigint NOT NULL,
    tg_username   text,
    claimed_name  text NOT NULL,              -- как человек представился
    app_user_id   integer,                    -- с кем связан при подтверждении
    state         text NOT NULL DEFAULT 'pending',   -- pending | approved | declined
    decided_by    integer,                    -- app_user.id руководителя отдела персонала
    decided_at    timestamptz,
    comment       text,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX tg_access_request_state_idx ON tg_access_request (state, created_at);

CREATE TABLE request_draft (
    id                bigserial PRIMARY KEY,
    number            text NOT NULL UNIQUE,     -- «Ч-2026-0042»: переносится в заявку 1С
    author_id         integer NOT NULL,         -- app_user.id нанимающего руководителя
    org_unit_id       integer REFERENCES org_unit(id),
    staff_position_id integer REFERENCES staff_position(id),
    role_code         text,                     -- профиль-основа; пусто — должности в базе нет
    title             text NOT NULL,
    headcount         smallint NOT NULL DEFAULT 1,
    reason            text,                     -- рост, замена, проект
    wanted_by         date,
    salary_min        integer,
    salary_max        integer,
    criteria          jsonb NOT NULL DEFAULT '{}',  -- отличия от профиля: must_have, nice_to_have,
                                                    -- stop_factors, keywords, вес; формат как в role_profile
    state             draft_state NOT NULL DEFAULT 'draft',
    round             smallint NOT NULL DEFAULT 0,  -- сколько раз руководитель просил новую подборку
    submitted_at      timestamptz,
    reviewed_by       integer,                  -- app_user.id руководителя отдела персонала
    reviewed_at       timestamptz,
    review_comment    text,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX request_draft_author_idx ON request_draft (author_id, state);

-- Что руководитель сказал боту. Голос расшифровывается в контуре, аудио
-- удаляется после расшифровки — хранится только текст.
CREATE TABLE request_draft_message (
    id          bigserial PRIMARY KEY,
    draft_id    bigint NOT NULL REFERENCES request_draft(id) ON DELETE CASCADE,
    kind        text NOT NULL,              -- voice | text | button
    transcript  text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- Кого бот показал руководителю и что тот ответил. По этим ответам уточняются
-- критерии черновика. Карточки уходят в Telegram (решение 24.10.2026), контакты — нет.
CREATE TABLE draft_candidate (
    draft_id    bigint NOT NULL REFERENCES request_draft(id) ON DELETE CASCADE,
    person_id   bigint NOT NULL REFERENCES person(id) ON DELETE CASCADE,
    round       smallint NOT NULL,
    score       numeric(5,2),
    verdict     text,                       -- like | dislike; пусто — не ответил
    reason      text,                       -- почему, своими словами; уходит в критерии
    shown_at    timestamptz NOT NULL DEFAULT now(),
    decided_at  timestamptz,
    PRIMARY KEY (draft_id, person_id)
);

ALTER TABLE vacancy_request
  ADD COLUMN draft_id bigint REFERENCES request_draft(id) ON DELETE SET NULL;  -- номер черновика из заявки 1С

CREATE TABLE interview (
    id            bigserial PRIMARY KEY,
    candidate_id  bigint NOT NULL REFERENCES candidate(id) ON DELETE CASCADE,
    vacancy_id    bigint REFERENCES vacancy(id) ON DELETE SET NULL,
    kind          text NOT NULL,          -- phone | recruiter | manager | tech_case
    scheduled_at  timestamptz NOT NULL,
    interviewer_id integer,
    result        text,                   -- pass | fail | no_show | rescheduled
    notes         text
);
CREATE INDEX interview_when_idx ON interview (scheduled_at);

-- ------------------------------------------------ оценки и сигналы

-- Одна строка на пару «человек × роль». Пересчитывается при событии по
-- резюме и при смене версии профиля. Балл — порядок просмотра, не вердикт,
-- поэтому features и explanation обязательны: в карточке видно, из чего он.
CREATE TABLE match (
    id              bigserial PRIMARY KEY,
    person_id       bigint NOT NULL REFERENCES person(id) ON DELETE CASCADE,
    role_profile_id integer NOT NULL REFERENCES role_profile(id),
    profile_version text NOT NULL,
    score           smallint NOT NULL CHECK (score BETWEEN 0 AND 100),
    features        jsonb NOT NULL DEFAULT '{}',  -- {"donor_company":20,"exp_years":12,...}
    explanation     text,                         -- разбор от модели: чего не хватает, что спросить
    model           text,
    scored_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (person_id, role_profile_id)
);
CREATE INDEX match_score_idx ON match (role_profile_id, score DESC);
CREATE INDEX match_person_idx ON match (person_id, score DESC);

CREATE TABLE alert (
    id              bigserial PRIMARY KEY,
    person_id       bigint REFERENCES person(id) ON DELETE CASCADE,
    role_profile_id integer REFERENCES role_profile(id),
    vacancy_id      bigint REFERENCES vacancy(id) ON DELETE CASCADE,
    reason          alert_reason NOT NULL,
    headline        text NOT NULL,        -- одна строка «почему сегодня»
    payload         jsonb NOT NULL DEFAULT '{}',
    created_at      timestamptz NOT NULL DEFAULT now(),
    state           text NOT NULL DEFAULT 'new',  -- new | taken | postponed | dismissed
    decided_by      integer,
    decided_at      timestamptz
);
CREATE INDEX alert_state_idx ON alert (state, created_at DESC);

-- Всё, что агент хочет сделать с человеком, проходит через эту таблицу.
-- Пункт 4.2 опросника: без подтверждения человека агент не делает ничего.
CREATE TABLE action_queue (
    id             bigserial PRIMARY KEY,
    kind           action_kind NOT NULL,
    person_id      bigint REFERENCES person(id) ON DELETE CASCADE,
    candidate_id   bigint REFERENCES candidate(id) ON DELETE CASCADE,
    vacancy_id     bigint REFERENCES vacancy(id) ON DELETE SET NULL,
    payload        jsonb NOT NULL DEFAULT '{}',   -- текст письма, время интервью, основание
    proposed_at    timestamptz NOT NULL DEFAULT now(),
    state          action_state NOT NULL DEFAULT 'proposed',
    decided_by     integer,
    decided_at     timestamptz,
    decline_reason text,                          -- на причинах отказа настраиваются веса
    executed_at    timestamptz,
    error          text
);
CREATE INDEX action_queue_state_idx ON action_queue (state, proposed_at);

CREATE TABLE notification (
    id          bigserial PRIMARY KEY,
    user_id     integer NOT NULL,
    kind        text NOT NULL,           -- digest | new_match | reply | stalled | interview | vacancy_starving
    body        jsonb NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    read_at     timestamptz,
    sent_email  boolean NOT NULL DEFAULT false
);
CREATE INDEX notification_user_idx ON notification (user_id, created_at DESC);

-- --------------------------------------------- пользователи и журнал

-- Пользователи приходят из 1С: логин, ФИО, подразделение и должность берутся
-- оттуда же, своих учёток система не заводит. Уволен в 1С — при ближайшей
-- выгрузке is_active = false, доступ закрывается.
CREATE TABLE app_user (
    id            serial PRIMARY KEY,
    external_id   text UNIQUE,                 -- идентификатор сотрудника в 1С
    login         text NOT NULL UNIQUE,        -- логин как в 1С
    full_name     text NOT NULL,
    email         text UNIQUE,
    department    text,                        -- подразделение из 1С
    position      text,                        -- должность из 1С
    org_unit_id   integer REFERENCES org_unit(id),  -- подразделение из оргструктуры 1С
    telegram_id   bigint UNIQUE,               -- привязывается после подтверждения доступа к боту
    role          user_role NOT NULL,          -- сопоставляется по должности, правится руководителем
    is_active     boolean NOT NULL DEFAULT true,
    synced_at     timestamptz,                 -- когда последний раз приходил в выгрузке
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX app_user_active_idx ON app_user (is_active, role);

-- Права ролей: что кому доступно. Значения читает и интерфейс, и API.
CREATE TABLE role_permission (
    role       user_role NOT NULL,
    permission text NOT NULL,   -- base.read | person.take | profile.edit | profile.approve |
                                -- digest.decide | settings.edit | report.read | access.manage |
                                -- org.read | org.map | draft.own | draft.read | draft.review
    PRIMARY KEY (role, permission)
);

INSERT INTO role_permission (role, permission) VALUES
  ('recruiter', 'base.read'), ('recruiter', 'person.take'), ('recruiter', 'profile.edit'),
  ('recruiter', 'feedback.write'), ('recruiter', 'dossier.export'),
  ('head_recruiter', 'base.read'), ('head_recruiter', 'person.take'), ('head_recruiter', 'profile.edit'),
  ('head_recruiter', 'profile.approve'), ('head_recruiter', 'digest.decide'),
  ('head_recruiter', 'settings.edit'), ('head_recruiter', 'report.read'),
  ('head_recruiter', 'access.manage'), ('head_recruiter', 'dossier.export'),
  ('recruiter', 'org.read'), ('head_recruiter', 'org.read'), ('head_recruiter', 'org.map'),
  ('recruiter', 'draft.read'), ('head_recruiter', 'draft.read'), ('head_recruiter', 'draft.review'),
  ('hiring_manager', 'vacancy.own'), ('hiring_manager', 'candidate.own'), ('hiring_manager', 'draft.own'),
  ('hiring_manager', 'base.read'),   -- решение 24.10.2026: вся база на чтение; контакты и решения — у рекрутера
  ('director', 'base.read'), ('director', 'report.read'), ('director', 'org.read'),
  ('agent', 'base.read'), ('agent', 'org.read')
ON CONFLICT DO NOTHING;

-- Журнал доступа к персональным данным: кто, когда и что открывал.
-- Нужен и по 152-ФЗ, и по-человечески — понять, кто уже звонил человеку.
CREATE TABLE audit_log (
    id          bigserial PRIMARY KEY,
    user_id     integer REFERENCES app_user(id),
    action      text NOT NULL,           -- view_contact | export | edit | merge | delete
    object_type text NOT NULL,
    object_id   bigint,
    detail      jsonb NOT NULL DEFAULT '{}',
    at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX audit_log_object_idx ON audit_log (object_type, object_id, at DESC);
CREATE INDEX audit_log_user_idx   ON audit_log (user_id, at DESC);

-- ------------------------------------------------ контур обучения

-- Чат обучения: рекрутеры пишут замечания, рекомендации и критику.
-- Сюда же автоматически попадают причины отказа и отсрочки из решений.
CREATE TABLE feedback_message (
    id           bigserial PRIMARY KEY,
    author_id    integer NOT NULL REFERENCES app_user(id),
    tag          feedback_tag NOT NULL,
    body         text NOT NULL CHECK (char_length(btrim(body)) >= 40),
    person_id    bigint REFERENCES person(id) ON DELETE SET NULL,
    role_profile_id integer REFERENCES role_profile(id),
    digest_id    bigint,                        -- в какую недельную сводку вошло
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX feedback_open_idx ON feedback_message (digest_id NULLS FIRST, created_at);
CREATE INDEX feedback_tag_idx  ON feedback_message (tag, created_at DESC);

-- Недельная сводка руководителю отдела персонала
CREATE TABLE feedback_digest (
    id          bigserial PRIMARY KEY,
    period_from date NOT NULL,
    period_to   date NOT NULL,
    sent_at     timestamptz,
    sent_to     integer REFERENCES app_user(id),
    messages    integer NOT NULL DEFAULT 0,     -- сколько сообщений вошло
    closed_at   timestamptz,
    UNIQUE (period_from, period_to)
);

-- Предложение агента внутри сводки плюс вопрос анкеты по нему
CREATE TABLE digest_proposal (
    id            bigserial PRIMARY KEY,
    digest_id     bigint NOT NULL REFERENCES feedback_digest(id) ON DELETE CASCADE,
    title         text NOT NULL,                -- что предлагается изменить
    rationale     text NOT NULL,                -- на скольких сообщениях основано
    question      text NOT NULL,                -- вопрос руководителю
    target        jsonb NOT NULL DEFAULT '{}',  -- что менять: профиль, вес, порог, шаблон
    decision      digest_decision NOT NULL DEFAULT 'pending',
    decided_by    integer REFERENCES app_user(id),
    decided_at    timestamptz,
    decision_note text,
    applied_at    timestamptz                   -- когда изменение реально вошло в работу
);
CREATE INDEX digest_proposal_state_idx ON digest_proposal (decision, digest_id);

ALTER TABLE feedback_message
  ADD CONSTRAINT feedback_digest_fk
  FOREIGN KEY (digest_id) REFERENCES feedback_digest(id) ON DELETE SET NULL;

-- Что изменилось в работе агента и по чьему решению
CREATE TABLE agent_change (
    id          bigserial PRIMARY KEY,
    proposal_id bigint REFERENCES digest_proposal(id) ON DELETE SET NULL,
    kind        text NOT NULL,                  -- weight | threshold | profile | template | source
    before      jsonb,
    after       jsonb,
    applied_by  integer REFERENCES app_user(id),
    applied_at  timestamptz NOT NULL DEFAULT now(),
    note        text
);
CREATE INDEX agent_change_when_idx ON agent_change (applied_at DESC);

-- Решение рекрутера по человеку. Комментарий обязателен и не короче 200 знаков:
-- «не наш» без объяснения ничему не учит, а причина — главный материал обучения.
CREATE TABLE decision_log (
    id         bigserial PRIMARY KEY,
    person_id  bigint NOT NULL REFERENCES person(id) ON DELETE CASCADE,
    kind       text NOT NULL,                   -- take | postpone | reject
    comment    text NOT NULL CHECK (char_length(btrim(comment)) >= 200),
    author_id  integer NOT NULL REFERENCES app_user(id),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX decision_log_person_idx ON decision_log (person_id, created_at DESC);

-- Журнал выгрузок досье: персональные данные уходят в файл, это учитывается
CREATE TABLE dossier_export (
    id         bigserial PRIMARY KEY,
    person_id  bigint REFERENCES person(id) ON DELETE SET NULL,
    candidate_id bigint REFERENCES candidate(id) ON DELETE SET NULL,
    format     text NOT NULL,                   -- docx | pdf
    scope      text NOT NULL DEFAULT 'full',    -- short | full
    author_id  integer NOT NULL REFERENCES app_user(id),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX dossier_export_when_idx ON dossier_export (created_at DESC);

-- Недельный отчёт об изменениях системы: профили, пороги, веса, настройки, права.
-- Уходит на личную почту руководителя отдела персонала и директора.
CREATE TABLE weekly_report (
    id           bigserial PRIMARY KEY,
    period_from  date NOT NULL,
    period_to    date NOT NULL,
    changes      integer NOT NULL DEFAULT 0,
    recipients   text[] NOT NULL DEFAULT '{}',
    body         jsonb NOT NULL DEFAULT '{}',   -- сгруппированные записи журнала
    sent_at      timestamptz,
    UNIQUE (period_from, period_to)
);

-- История файлов должностной инструкции: инструкция в компании меняется,
-- профиль пересобирается по новой редакции, старые остаются для сверки.
CREATE TABLE job_doc (
    id              bigserial PRIMARY KEY,
    role_profile_id integer NOT NULL REFERENCES role_profile(id) ON DELETE CASCADE,
    file_name       text NOT NULL,
    storage_key     text NOT NULL,
    revision        text,                        -- «ред. от 25.09.2026»
    sha256          text,
    uploaded_by     integer NOT NULL REFERENCES app_user(id),
    uploaded_at     timestamptz NOT NULL DEFAULT now(),
    parsed_at       timestamptz,                 -- когда агент разобрал файл
    proposal_id     bigint                       -- предложение правок профиля по новой редакции
);
CREATE INDEX job_doc_profile_idx ON job_doc (role_profile_id, uploaded_at DESC);

CREATE TABLE settings (
    key        text PRIMARY KEY,
    value      jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- Пороги вынесены в настройки: первый месяц подбираются по тому,
-- сколько карточек рекрутер успевает разобрать за утро.
INSERT INTO settings (key, value) VALUES
  ('alert_score_new',        '75'),
  ('alert_score_pool_event', '65'),
  ('alert_score_grew_by',    '10'),
  ('stalled_after_days',     '5'),
  ('reserve_touch_months',   '3'),
  ('digest_time',            '"08:30"'),
  ('daily_cards_limit',      '12'),
  ('watch_threshold',        '60'),
  ('social_search_enabled',  'true'),
  ('decision_comment_min',   '200'),
  ('feedback_min_chars',     '40'),
  ('digest_day',             '"friday"'),
  ('profile_editor',         '"any_recruiter"'),
  ('profile_approver',       '"head_recruiter"'),
  ('weekly_report_day',      '"friday"'),
  ('weekly_report_time',     '"17:00"'),
  ('keep_sourcing_default',  'true'),
  ('social_sources',         '["vk","ok","telegram","github","habr","linkedin","facebook","instagram","x","youtube","dzen","forum"]'),
  ('name_search_translit',   'true'),
  ('retention_candidate_years', '3'),
  ('retention_observation_months', '12'),
  ('org_sync_stale_hours',   '3'),    -- оргструктура старше — плашка в интерфейсе и сигнал в телеграм
  ('org_max_drop_pct',       '20'),   -- пропало больше этой доли позиций за раз — выгрузка отклоняется
  ('bot_shortlist_size',     '10'),   -- сколько кандидатов бот показывает за один раунд
  ('bot_min_score',          '60'),   -- ниже этого балла бот кандидатов не показывает
  ('draft_ready_remind_days','3')     -- черновик проверен, а заявки из 1С всё нет — напоминание
ON CONFLICT (key) DO NOTHING;

-- ------------------------------------------------------------ связи

ALTER TABLE person
  ADD CONSTRAINT person_candidate_fk
  FOREIGN KEY (candidate_id) REFERENCES candidate(id) ON DELETE SET NULL;

-- ------------------------------------------------------------- виды

-- Утренний дайджест: то, что попадает на экран «Сегодня»
CREATE VIEW v_today AS
SELECT a.id, a.reason, a.headline, a.created_at,
       p.label AS person_label, p.city,
       rp.title AS role_title, d.title AS department,
       m.score, m.explanation
FROM alert a
LEFT JOIN person p       ON p.id = a.person_id
LEFT JOIN role_profile rp ON rp.id = a.role_profile_id
LEFT JOIN department d    ON d.id = rp.department_id
LEFT JOIN match m         ON m.person_id = a.person_id AND m.role_profile_id = a.role_profile_id
WHERE a.state = 'new'
ORDER BY m.score DESC NULLS LAST, a.created_at DESC;

-- Открытые ставки по штатному расписанию: сопоставлена ли должность с профилем
-- и ведётся ли под позицию вакансия. «Открыто в штатке, вакансии нет» — сигнал рекрутеру.
CREATE VIEW v_open_positions AS
SELECT sp.id, sp.job_title, u.title AS unit, sp.rates_total, sp.rates_occupied, sp.rates_open,
       m.role_code,
       (m.job_external_id IS NULL) AS unmapped,
       EXISTS (SELECT 1 FROM vacancy v
               WHERE v.staff_position_id = sp.id AND v.state IN ('open', 'on_hold')) AS has_vacancy
FROM staff_position sp
JOIN org_unit u           ON u.id = sp.org_unit_id
LEFT JOIN job_role_map m  ON m.job_external_id = sp.job_external_id
WHERE sp.is_active AND sp.rates_open > 0;

-- Воронка по вакансиям: сколько человек и сколько дней стоят на этапе
CREATE VIEW v_funnel AS
SELECT v.id AS vacancy_id, v.title, c.status,
       count(*) AS people,
       round(avg(extract(epoch FROM now() - sh.changed_at) / 86400)::numeric, 1) AS avg_days_in_status
FROM vacancy v
JOIN action_queue aq ON aq.vacancy_id = v.id
JOIN candidate c     ON c.id = aq.candidate_id
LEFT JOIN LATERAL (
    SELECT changed_at FROM status_history
    WHERE candidate_id = c.id ORDER BY changed_at DESC LIMIT 1
) sh ON true
GROUP BY v.id, v.title, c.status;

-- Зависшие: молчат дольше порога из настроек
CREATE VIEW v_stalled AS
SELECT c.id, c.fio, c.status, c.owner_id, c.last_contact_at,
       (current_date - c.last_contact_at::date) AS days_silent
FROM candidate c
WHERE c.status IN ('first_message_sent', 'replied', 'phone_interview',
                   'manager_interview', 'test_task', 'security_check', 'offer')
  AND c.last_contact_at IS NOT NULL
  AND c.last_contact_at < now() - ((SELECT value::text::int FROM settings WHERE key = 'stalled_after_days') || ' days')::interval;
