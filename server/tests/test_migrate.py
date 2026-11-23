"""T1: схема и миграция core накатываются, бизнес-схема — ровно как в приёмке ТЗ (пункт 2)."""
from hr import db


def test_public_has_47_tables_and_4_views(conn):
    """46 таблиц приёмки ТЗ (пункт 2) и role_synonym из 010_semantic_search.sql."""
    tables = db.val(conn, """SELECT count(*) FROM information_schema.tables
                             WHERE table_schema = 'public' AND table_type = 'BASE TABLE'""")
    views = db.val(conn, "SELECT count(*) FROM information_schema.views WHERE table_schema = 'public'")
    assert (tables, views) == (47, 4)


def test_ops_service_tables(conn):
    names = {r["table_name"] for r in db.rows(conn, """SELECT table_name FROM information_schema.tables
                                                        WHERE table_schema = 'ops'""")}
    assert {"schema_migration", "job_run", "oauth_token", "oauth_state", "import_file"} <= names


def test_sources_seeded(conn):
    src = {r["code"]: r for r in db.rows(conn, "SELECT code, kind, mode, is_active FROM source")}
    assert set(src) >= {"hh", "trudvsem", "habr", "avito", "manual"}
    assert src["hh"]["is_active"] and src["hh"]["mode"] == "api"
    assert src["trudvsem"]["is_active"], "«Работа России» включена миграцией 004 (решение человека 2026-10-30)"
    assert not src["avito"]["is_active"]
    assert src["habr"]["mode"] == "cabinet_export"


def test_core_settings(conn):
    for key in ("stop_factor_cap", "must_penalty_pct", "must_penalty_max_pct", "donor_alert_departments",
                "local_area_cities", "hh_full_resume_per_run", "hh_recheck_per_run", "crawl_max_pages",
                "merge_auto_similarity", "merge_ask_similarity", "score_grew_after_months"):
        assert db.setting(conn, key) is not None, key
    assert db.setting(conn, "stop_factor_cap") == 40
    assert db.setting(conn, "must_penalty_pct") == 10


def test_migrate_is_idempotent(conn):
    assert db.migrate() == []


def test_workplace_ops_tables(conn):
    """T1 workplace: служебное рабочего места — только в ops."""
    names = {r["table_name"] for r in db.rows(conn, """SELECT table_name FROM information_schema.tables
                                                        WHERE table_schema = 'ops'""")}
    assert {"user_password", "web_session", "person_photo", "llm_cache", "feedback_reply",
            "candidate_vacancy"} <= names


def test_workplace_permissions_logged(conn):
    perms = {(r["role"], r["permission"]) for r in db.rows(conn, "SELECT role::text, permission FROM role_permission")}
    for p in (("recruiter", "contact.open"), ("recruiter", "vacancy.edit"), ("head_recruiter", "contact.open"),
              ("head_recruiter", "vacancy.edit"), ("head_recruiter", "feedback.write")):
        assert p in perms, p
    assert ("hiring_manager", "contact.open") not in perms
    assert db.val(conn, "SELECT count(*) FROM agent_change WHERE kind = 'permission'") == 4  # 002, 007, 010, 012


def test_workplace_settings(conn):
    for key in ("session_idle_hours", "session_max_days", "login_max_failures", "login_lock_minutes",
                "photo_max_bytes", "preview_max_persons", "llm_pause_minutes", "llm_calls_per_hour",
                "base_page_size"):
        assert db.setting(conn, key) is not None, key
    assert db.setting(conn, "photo_max_bytes") == 10240


def test_photo_over_10kb_rejected(conn):
    import psycopg
    import pytest
    from factories import hh_item, source

    from hr.clock import SYSTEM
    from hr.normalize import normalize
    from hr.pipeline import process
    it = hh_item()
    r = process(conn, source(conn, "hh"), normalize("hh", it), it, SYSTEM.now())
    with pytest.raises(psycopg.errors.CheckViolation):
        with conn.transaction():
            db.run(conn, "INSERT INTO ops.person_photo (observation_id, data, state) VALUES (%s, %s, 'ok')",
                   (r.observation_id, b"x" * 10241))


def test_manager_cabinet_tables_and_rights(conn):
    """T1 manager-cabinet: новое — в ops, права по матрице спеки, роль no_access без прав."""
    names = {r["table_name"] for r in db.rows(conn, """SELECT table_name FROM information_schema.tables
                                                        WHERE table_schema = 'ops'""")}
    assert {"user_sync", "user_override", "invite_code", "handoff", "user_tweak", "user_match"} <= names
    perms = {(r["role"], r["permission"]) for r in db.rows(conn, "SELECT role::text, permission FROM role_permission")}
    for role in ("recruiter", "head_recruiter"):
        for p in ("base.all", "collect.run", "handoff.review", "invite.issue", "digest.read", "tweak.own"):
            assert (role, p) in perms, (role, p)
    assert ("head_recruiter", "handoff.all") in perms and ("recruiter", "handoff.all") not in perms
    for role in ("hiring_manager", "director"):
        for p in ("base.read", "profile.edit", "handoff.create", "tweak.own"):
            assert (role, p) in perms, (role, p)
        for p in ("base.all", "contact.open", "person.take", "collect.run", "settings.edit"):
            assert (role, p) not in perms, (role, p)
    assert not db.rows(conn, "SELECT 1 FROM role_permission WHERE role = 'no_access'")
    assert db.setting(conn, "handoff_comment_min") == 50 and db.setting(conn, "invite_code_hours") == 72


def test_semantic_search_tables_and_settings(conn):
    """T1 semantic-search (S2, S11, S12): синонимы, векторы профилей, суточные пределы, здоровье служб."""
    ops = {r["table_name"] for r in db.rows(conn, """SELECT table_name FROM information_schema.tables
                                                      WHERE table_schema = 'ops'""")}
    assert {"profile_embedding", "daily_quota", "service_health"} <= ops
    cols = {r["column_name"]: r for r in db.rows(conn, """SELECT column_name, is_nullable, data_type
                                                           FROM information_schema.columns
                                                           WHERE table_schema = 'public'
                                                             AND table_name = 'role_synonym'""")}
    assert set(cols) == {"role_code", "keyword", "synonyms", "updated_by", "updated_at"}
    assert cols["synonyms"]["data_type"] == "ARRAY" and cols["updated_at"]["is_nullable"] == "NO"
    assert db.val(conn, """SELECT format_type(atttypid, atttypmod) FROM pg_attribute
                           WHERE attrelid = 'ops.profile_embedding'::regclass AND attname = 'embedding'""") \
        == "vector(1024)"
    expected = {"hh_full_resume_per_day": 300, "llm_calls_per_day": 500, "llm_synonyms_per_day": 100,
                "llm_assist_enabled": True, "semantic_weight_default": 15, "semantic_sim_floor": 0.40,
                "semantic_sim_full": 0.75, "hh_query_max_terms": 20, "embed_batch_size": 32,
                "embed_night_until": "06:30"}
    assert {k: db.setting(conn, k) for k in expected} == expected
    logged = {r["after"]["setting"] for r in db.rows(conn, """SELECT after FROM agent_change
                                                               WHERE note LIKE 'выпуск 010%%'""")}
    assert set(expected) <= logged


def test_semantic_search_synonyms_permission_logged(conn):
    """T1 semantic-search: синонимы правят только рекрутеры; руководители с profile.edit — только видят."""
    perms = {(r["role"], r["permission"]) for r in db.rows(conn, "SELECT role::text, permission FROM role_permission")}
    assert ("recruiter", "profile.synonyms") in perms and ("head_recruiter", "profile.synonyms") in perms
    assert ("hiring_manager", "profile.synonyms") not in perms and ("director", "profile.synonyms") not in perms
    added = db.val(conn, """SELECT after->'add' FROM agent_change
                            WHERE kind = 'permission' AND note LIKE 'миграция 010%%'""")
    assert sorted(added) == ["head_recruiter:profile.synonyms", "recruiter:profile.synonyms"]


def test_daily_quota_take_is_atomic_upsert(conn):
    """T1 semantic-search (S11): ключ (day, key) уникален — счёт ведётся одной строкой."""
    sql = """INSERT INTO ops.daily_quota (day, key, used) VALUES ('2026-11-22', 'hh_full_resume', 1)
             ON CONFLICT (day, key) DO UPDATE SET used = ops.daily_quota.used + 1
             WHERE ops.daily_quota.used < %s RETURNING used"""
    assert [db.val(conn, sql, (2,)) for _ in range(3)] == [1, 2, None]


# --- funnel-report T1: 012_funnel_stages.sql ---

FUNNEL_ROWS = [("selected", "Отобранные резюме"), ("contact", "Первичный контакт"),
               ("screening", "Пройденное первичное интервью"), ("interview", "Интервью"),
               ("meeting", "Собеседование"), ("security", "Проверка СБ"), ("offer", "Оффер"),
               ("first_day", "Пришедшие в 1 день")]


def _vac_cand(conn):
    from factories import profile
    vid = db.val(conn, "INSERT INTO vacancy (role_profile_id, title) VALUES (%s, 'Инженер') RETURNING id",
                 (profile(conn)["id"],))
    cid = db.val(conn, """INSERT INTO candidate (fio, retention_until) VALUES ('Вымышленный В. В.', current_date + 100)
                          RETURNING id""")
    return vid, cid


def test_s1_stage_library_rows_and_standard_stages(conn):
    """S1: 8 строк отчёта по порядку; в справочнике 8 стандартных (по одному на строку) и дополнительные."""
    names = {r["table_name"] for r in db.rows(conn, """SELECT table_name FROM information_schema.tables
                                                        WHERE table_schema = 'ops'""")}
    assert {"funnel_row", "fail_reason", "stage", "stage_set", "stage_set_item", "candidate_stage",
            "vacancy_pause"} <= names
    rows = db.rows(conn, "SELECT code, title FROM ops.funnel_row ORDER BY ord")
    assert [(r["code"], r["title"]) for r in rows] == FUNNEL_ROWS
    std = db.rows(conn, "SELECT title, report_row FROM ops.stage WHERE is_standard AND is_active ORDER BY id")
    assert [(r["report_row"], r["title"]) for r in std] == FUNNEL_ROWS
    extra = {r["title"]: r["report_row"] for r in db.rows(conn, "SELECT title, report_row FROM ops.stage"
                                                                " WHERE NOT is_standard")}
    assert extra == {"Тестовое задание": "interview", "Оплачиваемое тестовое": "interview",
                     "Собеседование с руководителем": "meeting"}


def test_s1_stage_needs_known_report_row_and_unique_title(conn):
    import psycopg
    import pytest
    with pytest.raises(psycopg.errors.ForeignKeyViolation), conn.transaction():
        db.run(conn, "INSERT INTO ops.stage (title, report_row) VALUES ('Этап без строки', 'nowhere')")
    with pytest.raises(psycopg.errors.UniqueViolation), conn.transaction():
        db.run(conn, "INSERT INTO ops.stage (title, report_row) VALUES ('Оффер', 'offer')")


def test_s7_fail_reasons_by_outcome(conn):
    """S7: «не прошёл» — отказали мы, «отказался» — сам; «другое» у обоих с комментарием; оффер — только на «Оффере»."""
    rs = {r["code"]: r for r in db.rows(conn, "SELECT * FROM ops.fail_reason ORDER BY ord")}
    assert len(rs) == 9
    by = {o: {r["title"] for r in rs.values() if r["outcome"] == o} for o in ("failed", "refused")}
    assert by["failed"] == {"Опыт или навыки", "Тестовое задание", "Проверка СБ"}
    assert by["refused"] == {"Не подходят условия труда", "Отказ от оффера", "Не вышел на связь",
                             "Не пришёл на встречу", "Не вышел в первый день"}
    assert rs["other"]["outcome"] is None and rs["other"]["needs_comment"] and rs["other"]["title"] == "Другое"
    assert [c for c, r in rs.items() if r["only_row"]] == ["offer_declined"]
    assert rs["offer_declined"]["only_row"] == "offer"
    assert [c for c, r in rs.items() if r["needs_comment"]] == ["other"]


def test_s1_stage_approve_permission_logged(conn):
    """Право утверждать наборы этапов — только у руководителя ОП, запись в журнале изменений."""
    perms = {(r["role"], r["permission"]) for r in db.rows(conn, "SELECT role::text, permission FROM role_permission")}
    assert {p for p in perms if p[1] == "stage.approve"} == {("head_recruiter", "stage.approve")}
    added = db.val(conn, """SELECT after->'add' FROM agent_change
                            WHERE kind = 'permission' AND note LIKE 'миграция 012%%'""")
    assert added == ["head_recruiter:stage.approve"]


def test_s7_other_comment_min_setting(conn):
    assert db.setting(conn, "stage_fail_other_min") == 100
    assert db.val(conn, """SELECT count(*) FROM agent_change
                           WHERE note LIKE 'выпуск 012%%' AND after->>'setting' = 'stage_fail_other_min'""") == 1


def test_stage_set_one_proposed_per_vacancy(conn):
    """S3: на утверждении у вакансии не больше одного набора — на уровне СУБД; версии уникальны."""
    import psycopg
    import pytest
    vid, _ = _vac_cand(conn)
    ins = "INSERT INTO ops.stage_set (vacancy_id, version, state) VALUES (%s, %s, %s) RETURNING id"
    db.val(conn, ins, (vid, 1, "approved"))
    db.val(conn, ins, (vid, 2, "proposed"))
    with pytest.raises(psycopg.errors.ExclusionViolation), conn.transaction():
        db.val(conn, ins, (vid, 3, "proposed"))
    with pytest.raises(psycopg.errors.UniqueViolation), conn.transaction():
        db.val(conn, ins, (vid, 2, "declined"))
    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
        db.val(conn, ins, (vid, 4, "draft"))
    db.val(conn, ins, (vid, 3, "withdrawn"))


def test_candidate_stage_one_open_mark_and_reason(conn):
    """S6–S7: одна открытая отметка на пару, слот пары — один раз; не «прошёл» — с причиной; отменённые не мешают."""
    import psycopg
    import pytest
    vid, cid = _vac_cand(conn)
    sid = db.val(conn, "SELECT id FROM ops.stage WHERE title = 'Первичный контакт'")
    ins = """INSERT INTO ops.candidate_stage (candidate_id, vacancy_id, slot, stage_id, report_row, outcome, reason)
             VALUES (%s, %s, %s, %s, 'contact', %s, %s) RETURNING id"""
    first = db.val(conn, ins, (cid, vid, 2, sid, None, None))
    with pytest.raises(psycopg.errors.ExclusionViolation), conn.transaction():
        db.val(conn, ins, (cid, vid, 3, sid, None, None))
    with pytest.raises(psycopg.errors.ExclusionViolation), conn.transaction():
        db.val(conn, ins, (cid, vid, 2, sid, "passed", None))
    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
        db.run(conn, "UPDATE ops.candidate_stage SET outcome = 'failed' WHERE id = %s", (first,))
    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
        db.run(conn, "UPDATE ops.candidate_stage SET outcome = 'maybe', reason = 'skills' WHERE id = %s", (first,))
    db.run(conn, "UPDATE ops.candidate_stage SET cancelled_at = now() WHERE id = %s", (first,))
    db.val(conn, ins, (cid, vid, 2, sid, None, None))  # открытая копия после отмены


def test_s14_vacancy_pause_one_open(conn):
    """S14: у вакансии одна открытая пауза."""
    import psycopg
    import pytest
    vid, _ = _vac_cand(conn)
    ins = "INSERT INTO ops.vacancy_pause (vacancy_id, from_date, to_date, kind) VALUES (%s, %s, %s, %s)"
    db.run(conn, ins, (vid, "2026-11-01", "2026-11-05", "pause"))
    db.run(conn, ins, (vid, "2026-11-10", None, "pause"))
    with pytest.raises(psycopg.errors.ExclusionViolation), conn.transaction():
        db.run(conn, ins, (vid, "2026-11-12", None, "closed_gap"))
    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
        db.run(conn, ins, (vid, "2026-11-12", "2026-11-11", "pause"))
