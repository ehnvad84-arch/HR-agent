"""T7: действующая версия профиля, порог наблюдения, заведение карточки (S12, S13, S17)."""
from datetime import UTC, datetime, timedelta

from factories import hh_item, profile, source, weak_item

from hr import db
from hr.normalize import normalize
from hr.pipeline import current_profiles, process


def ingest(conn, it, clock, code="hh"):
    src = source(conn, code)
    return process(conn, src, normalize(code, it), it, clock.now())


def test_s12_strong_resume_creates_person(conn, clock):
    pr = profile(conn)
    r = ingest(conn, hh_item(), clock)
    assert r.person_created and r.person_id
    p = db.one(conn, "SELECT * FROM person WHERE id = %s", (r.person_id,))
    assert p["label"] == "Ведущий инженер-электронщик, Краснодар"
    assert "Тестовый Андрей" in p["name_variants"]
    ev = db.rows(conn, "SELECT kind FROM obs_event WHERE person_id = %s", (r.person_id,))
    assert [e["kind"] for e in ev] == ["new"]
    m = db.one(conn, "SELECT * FROM match WHERE person_id = %s AND role_profile_id = %s", (r.person_id, pr["id"]))
    assert m["score"] >= 60 and m["features"] and m["explanation"] and m["model"] == "template"
    assert m["profile_version"] == "1.0"


def test_s12_scores_by_all_current_profiles(conn, clock):
    profile(conn)
    profile(conn, code="otk_engineer", title="Инженер ОТК", dept="qc", keywords=["AQL"])
    r = ingest(conn, hh_item(), clock)
    assert db.val(conn, "SELECT count(*) FROM match WHERE person_id = %s", (r.person_id,)) == 2


def test_s13_weak_resume_stays_without_card(conn, clock):
    profile(conn)
    r = ingest(conn, weak_item(), clock)
    assert not r.person_created and r.person_id is None
    obs = db.one(conn, "SELECT person_id FROM observation WHERE id = %s", (r.observation_id,))
    assert obs["person_id"] is None
    assert db.val(conn, "SELECT count(*) FROM person") == 0
    assert db.val(conn, "SELECT count(*) FROM obs_event") == 0


def test_s13_later_change_above_threshold_creates_card_with_new(conn, clock):
    profile(conn)
    ingest(conn, weak_item(), clock)
    clock.advance(days=30)
    better = hh_item(id="weak-1")
    r = ingest(conn, better, clock)
    assert r.person_created
    kinds = [e["kind"] for e in db.rows(conn, "SELECT kind FROM obs_event WHERE person_id = %s", (r.person_id,))]
    assert kinds == ["new"]


def test_s17_unapproved_version_not_used(conn, clock):
    old = profile(conn, version="1.0", keywords=["Altium"],
                  approved_at=datetime.now(UTC) - timedelta(days=5))
    draft = profile(conn, version="1.1", approved=False, keywords=["COBOL"])
    cur = {p["code"]: p["id"] for p in current_profiles(conn)}
    assert cur["engineer_hw_lead"] == old["id"] != draft["id"]


def test_s17_latest_approved_version_wins(conn):
    profile(conn, version="1.0", approved_at=datetime.now(UTC) - timedelta(days=5))
    new = profile(conn, version="1.1")
    assert [p["id"] for p in current_profiles(conn)] == [new["id"]]


def test_s17_profile_without_approved_version_is_not_scored(conn, clock):
    profile(conn)
    profile(conn, code="draft_only", title="Инженер-электронщик", approved=False)
    r = ingest(conn, hh_item(), clock)
    codes = {x["code"] for x in db.rows(conn, """SELECT rp.code FROM match m
                                                 JOIN role_profile rp ON rp.id = m.role_profile_id
                                                 WHERE m.person_id = %s""", (r.person_id,))}
    assert codes == {"engineer_hw_lead"}


def test_no_profiles_nothing_created(conn, clock):
    r = ingest(conn, hh_item(), clock)
    assert r.person_id is None and r.observation_id
