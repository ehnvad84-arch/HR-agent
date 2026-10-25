"""T11: сигналы «обратите внимание» (S21–S27)."""
from datetime import timedelta

from factories import hh_item, profile, source

from hr import db
from hr.normalize import normalize
from hr.pipeline import process

COMMENT = "Отказ по делу: " + "подробное объяснение причины отказа для обучения агента. " * 5


def ingest(conn, it, clock):
    return process(conn, source(conn, "hh"), normalize("hh", it), it, clock.now())


def alerts(conn, pid=None):
    sql = "SELECT * FROM alert" + (" WHERE person_id = %s" if pid else "") + " ORDER BY id"
    return db.rows(conn, sql, (pid,) if pid else None)


def set_(conn, key, value):
    db.run(conn, "UPDATE settings SET value = %s WHERE key = %s", (db.j(value), key))


def changed(**kw):
    it = hh_item(**kw)
    it["salary"] = {"amount": 175000}
    return it


def test_s21_new_strong_person_gets_new_high_score(conn, clock):
    profile(conn)
    r = ingest(conn, hh_item(), clock)
    a = alerts(conn, r.person_id)
    assert [x["reason"] for x in a] == ["new_high_score"]
    assert "Ведущий инженер-электронщик" in a[0]["headline"] and a[0]["payload"]["score"] >= 75


def test_s21_new_below_75_no_alert(conn, clock):
    profile(conn)
    set_(conn, "alert_score_new", 101)
    r = ingest(conn, hh_item(), clock)
    assert r.person_id and alerts(conn, r.person_id) == []


def test_s22_event_on_pool(conn, clock):
    profile(conn)
    r = ingest(conn, hh_item(), clock)
    db.run(conn, "UPDATE alert SET state = 'taken'")
    clock.advance(days=1)
    ingest(conn, changed(), clock)
    a = [x for x in alerts(conn, r.person_id) if x["state"] == "new"]
    assert [x["reason"] for x in a] == ["event_on_pool"]
    assert "ожидания" in a[0]["headline"].lower()


def test_s23_watched_gets_alert_below_thresholds(conn, clock):
    profile(conn)
    set_(conn, "alert_score_new", 101)
    set_(conn, "alert_score_pool_event", 101)
    r = ingest(conn, hh_item(), clock)
    db.run(conn, "UPDATE person SET watch = 'watched' WHERE id = %s", (r.person_id,))
    clock.advance(days=1)
    ingest(conn, changed(), clock)
    assert [x["reason"] for x in alerts(conn, r.person_id)] == ["event_on_pool"]


def _reject(conn, pid, role_profile_id, score_at, when):
    uid = db.val(conn, """INSERT INTO app_user (login, full_name, role)
                          VALUES ('r1', 'Рекрутер', 'recruiter') RETURNING id""")
    db.run(conn, """INSERT INTO decision_log (person_id, kind, comment, author_id, created_at, score_at,
                                              role_profile_id)
                    VALUES (%s, 'reject', %s, %s, %s, %s, %s)""", (pid, COMMENT, uid, when, score_at, role_profile_id))
    db.run(conn, "UPDATE person SET watch = 'dropped' WHERE id = %s", (pid,))
    db.run(conn, "UPDATE alert SET state = 'dismissed'")


def test_s24_score_grew_after_reject_compared_by_code(conn, clock):
    v1 = profile(conn)
    r = ingest(conn, hh_item(), clock)
    _reject(conn, r.person_id, v1["id"], score_at=50, when=clock.now() - timedelta(days=200))
    profile(conn, version="1.1")          # новая версия: сравнение идёт по коду профиля
    clock.advance(days=1)
    ingest(conn, changed(), clock)
    a = [x for x in alerts(conn, r.person_id) if x["state"] == "new"]
    assert [x["reason"] for x in a] == ["score_grew"]
    assert "с 50 до" in a[0]["headline"]


def test_s24_recent_reject_or_small_growth_no_alert(conn, clock):
    v1 = profile(conn)
    r = ingest(conn, hh_item(), clock)
    score = db.val(conn, "SELECT score FROM match WHERE person_id = %s", (r.person_id,))
    _reject(conn, r.person_id, v1["id"], score_at=score - 5, when=clock.now() - timedelta(days=200))
    clock.advance(days=1)
    ingest(conn, changed(), clock)
    assert [x for x in alerts(conn, r.person_id) if x["state"] == "new"] == []


def test_s25_donor_company(conn, clock):
    profile(conn, code="sales_mgr", title="Менеджер проектных продаж", dept="sales",
            keywords=["Altium"], donor_companies=["НПП Тест"])
    set_(conn, "alert_score_new", 101)
    set_(conn, "alert_score_pool_event", 101)
    r = ingest(conn, hh_item(), clock)
    clock.advance(days=1)
    ingest(conn, changed(), clock)
    a = alerts(conn, r.person_id)
    assert [x["reason"] for x in a] == ["donor_company"] and "НПП Тест" in a[0]["headline"]


def test_s26_postponed_and_candidate_get_nothing(conn, clock):
    profile(conn)
    set_(conn, "alert_score_new", 101)
    r = ingest(conn, hh_item(), clock)
    db.run(conn, "UPDATE person SET watch = 'watched', postponed_until = %s WHERE id = %s",
           ((clock.now() + timedelta(days=30)).date(), r.person_id))
    clock.advance(days=1)
    ingest(conn, changed(), clock)
    assert alerts(conn, r.person_id) == [], "отложенный не получает сигнала и при «веду особо»"


def test_s27_one_open_alert_per_person_and_profile_code(conn, clock):
    profile(conn)
    r = ingest(conn, hh_item(), clock)
    first = alerts(conn, r.person_id)[0]
    v2 = profile(conn, version="1.1")
    clock.advance(days=1)
    ingest(conn, changed(), clock)
    a = alerts(conn, r.person_id)
    assert len(a) == 1 and a[0]["id"] == first["id"]
    assert a[0]["reason"] == "event_on_pool" and a[0]["role_profile_id"] == v2["id"]
    assert a[0]["created_at"] > first["created_at"]


def test_events_marked_processed(conn, clock):
    profile(conn)
    ingest(conn, hh_item(), clock)
    assert db.val(conn, "SELECT count(*) FROM obs_event WHERE NOT processed") == 0
