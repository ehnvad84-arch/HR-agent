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


# --- funnel-report T2: перенос прежних кандидатов (S15) ---

CARRY_ROW = {"picked_by_agent": "selected", "first_message_sent": "contact", "replied": "contact",
             "phone_interview": "screening", "manager_interview": "meeting", "test_task": "meeting",
             "security_check": "security", "offer": "offer"}
NO_CARRY = ["new", "reserve", "hired", "no_show", "refused_contact", "rejected_by_us", "rejected_by_candidate"]


def _vacancy(conn, state="open", title="Инженер"):
    from factories import profile
    closed = state in ("closed", "cancelled")
    pid = db.val(conn, "SELECT id FROM role_profile WHERE code = 'engineer_hw_lead'") or profile(conn)["id"]
    return db.val(conn, """INSERT INTO vacancy (role_profile_id, title, state, closed_at, close_comment)
                           VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                  (pid, title, state, "2026-11-20" if closed else None,
                   "Вымышленная причина закрытия. " * 20 if closed else None))


def _candidate(conn, status, *vacancies):
    """Кандидат со статусом; связи с вакансиями по порядку — последняя самая поздняя по added_at."""
    cid = db.val(conn, """INSERT INTO candidate (fio, status, retention_until)
                          VALUES ('Вымышленный В. В.', %s, current_date + 100) RETURNING id""", (status,))
    for i, vid in enumerate(vacancies):
        db.run(conn, """INSERT INTO ops.candidate_vacancy (candidate_id, vacancy_id, added_at)
                        VALUES (%s, %s, timestamptz '2026-11-01 10:00+03' + %s * interval '1 day')""", (cid, vid, i))
    return cid


def _marks(conn, cid=None):
    return db.rows(conn, """SELECT cs.candidate_id, cs.vacancy_id, cs.slot, cs.report_row, cs.carried, cs.outcome,
                                   s.title AS stage
                            FROM ops.candidate_stage cs JOIN ops.stage s ON s.id = cs.stage_id
                            WHERE cs.cancelled_at IS NULL AND (%s::bigint IS NULL OR cs.candidate_id = %s)
                            ORDER BY cs.candidate_id, cs.vacancy_id""", (cid, cid))


def _carry(conn):
    return db.one(conn, "SELECT * FROM ops.funnel_carry()")


def test_s15_carry_gives_open_vacancies_standard_set_v1(conn):
    """S15: вакансиям open|on_hold — утверждённый набор v1 из 8 стандартных этапов; закрытым и с набором — нет."""
    open_, hold, closed, cancelled, has_set = (_vacancy(conn, s) for s in ("open", "on_hold", "closed",
                                                                         "cancelled", "open"))
    db.run(conn, "INSERT INTO ops.stage_set (vacancy_id, version, state) VALUES (%s, 1, 'approved')", (has_set,))
    assert _carry(conn)["sets"] == 2
    sets = db.rows(conn, """SELECT vacancy_id, version, state, decided_at IS NOT NULL AS decided FROM ops.stage_set
                            WHERE vacancy_id = ANY(%s) ORDER BY vacancy_id""",
                   ([open_, hold, closed, cancelled, has_set],))
    assert [(s["vacancy_id"], s["version"], s["state"]) for s in sets] == [
        (open_, 1, "approved"), (hold, 1, "approved"), (has_set, 1, "approved")]
    assert sets[0]["decided"]
    items = db.rows(conn, """SELECT i.pos, i.slot, s.title, s.report_row, s.is_standard
                             FROM ops.stage_set_item i JOIN ops.stage_set ss ON ss.id = i.set_id
                             JOIN ops.stage s ON s.id = i.stage_id WHERE ss.vacancy_id = %s ORDER BY i.pos""",
                     (open_,))
    assert [(i["pos"], i["slot"], i["report_row"], i["title"]) for i in items] == [
        (n, n, code, title) for n, (code, title) in enumerate(FUNNEL_ROWS, 1)]
    assert all(i["is_standard"] for i in items)


def test_s15_carry_puts_open_pairs_on_stage_by_status(conn):
    """S15: открытые — на этап своей строки без истории (carried); новые, резерв и закрытые — нет."""
    vid, closed = _vacancy(conn), _vacancy(conn, "closed")
    carried = {status: _candidate(conn, status, vid) for status in CARRY_ROW}
    skipped = [_candidate(conn, status, vid) for status in NO_CARRY] + [_candidate(conn, "offer", closed)]
    res = _carry(conn)
    assert res == {"sets": 1, "marks": len(CARRY_ROW)}
    got = {m["candidate_id"]: m for m in _marks(conn)}
    assert {status: got[cid]["report_row"] for status, cid in carried.items()} == CARRY_ROW
    slot = dict(zip([c for c, _ in FUNNEL_ROWS], range(1, 9), strict=True))
    assert all(m["slot"] == slot[m["report_row"]] and m["carried"] and m["outcome"] is None
               and m["vacancy_id"] == vid for m in got.values())
    assert not set(skipped) & set(got)


def test_s15_carry_several_vacancies_status_goes_to_latest(conn):
    """S15: кандидат в нескольких вакансиях — статус в последнюю по связи, в остальные — «Отобранные резюме»."""
    a, b, c = _vacancy(conn, title="А"), _vacancy(conn, "on_hold", title="Б"), _vacancy(conn, title="В")
    cid = _candidate(conn, "security_check", a, c, b)     # b — последняя связь
    _carry(conn)
    assert {m["vacancy_id"]: m["report_row"] for m in _marks(conn, cid)} == {
        a: "selected", c: "selected", b: "security"}


def test_s15_carry_latest_link_closed_others_selected(conn):
    """S15: последняя связь — закрытая вакансия: там отметок нет, в открытых — «Отобранные резюме»."""
    a, closed = _vacancy(conn), _vacancy(conn, "closed")
    cid = _candidate(conn, "offer", a, closed)
    _carry(conn)
    assert [(m["vacancy_id"], m["report_row"]) for m in _marks(conn, cid)] == [(a, "selected")]


def test_s15_carry_repeat_changes_nothing(conn):
    """S15: повтор ничего не меняет; пары, у которых уже есть отметки, не трогаются."""
    vid, other = _vacancy(conn), _vacancy(conn)
    cid = _candidate(conn, "offer", vid)
    marked = _candidate(conn, "phone_interview", other)
    _carry(conn)
    before = _marks(conn)
    db.run(conn, "UPDATE candidate SET status = 'security_check' WHERE id = %s", (cid,))
    assert _carry(conn) == {"sets": 0, "marks": 0}
    assert _marks(conn) == before
    assert db.val(conn, "SELECT count(*) FROM ops.stage_set WHERE vacancy_id = %s", (vid,)) == 1
    assert {m["candidate_id"] for m in before} == {cid, marked}


def test_s15_carry_runs_in_migration_012(conn):
    """S15: функцию вызывает 012 — повторный прогон файла переносит данные, накопленные до него."""
    vid = _vacancy(conn)
    cid = _candidate(conn, "replied", vid)
    db.run(conn, (db.MIGRATIONS_DIR / "012_funnel_stages.sql").read_text(encoding="utf-8"))
    assert [(m["vacancy_id"], m["report_row"], m["carried"]) for m in _marks(conn, cid)] == [(vid, "contact", True)]


def test_s15_cli_funnel_carry(conn, conn_factory, monkeypatch, capsys):
    """Откат и возврат на v1.3.0: python -m hr.cli funnel-carry переносит то, что изменила прежняя версия."""
    from hr import cli
    monkeypatch.setattr(db, "tx", conn_factory)
    vid = _vacancy(conn)
    cid = _candidate(conn, "manager_interview", vid)
    assert cli.main(["funnel-carry"]) == 0
    out = capsys.readouterr().out
    assert "наборов этапов: 1" in out and "отметок: 1" in out
    assert [m["report_row"] for m in _marks(conn, cid)] == ["meeting"]
    assert cli.main(["funnel-carry"]) == 0
    assert "наборов этапов: 0" in capsys.readouterr().out
