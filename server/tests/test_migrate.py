"""T1: схема и миграция core накатываются, бизнес-схема — ровно как в приёмке ТЗ (пункт 2)."""
from hr import db


def test_public_has_46_tables_and_4_views(conn):
    tables = db.val(conn, """SELECT count(*) FROM information_schema.tables
                             WHERE table_schema = 'public' AND table_type = 'BASE TABLE'""")
    views = db.val(conn, "SELECT count(*) FROM information_schema.views WHERE table_schema = 'public'")
    assert (tables, views) == (46, 4)


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
    assert db.val(conn, "SELECT count(*) FROM agent_change WHERE kind = 'permission'") == 2


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
