"""T9: новая версия профиля переоценивает базу (S20)."""
import pytest
from factories import hh_item, profile, source

from hr import db
from hr.normalize import normalize
from hr.pipeline import ProfileNotApproved, process, rescore_profile


def ingest(conn, it, clock):
    return process(conn, source(conn, "hh"), normalize("hh", it), it, clock.now())


def test_s20_all_people_rescored_including_dropped(conn, conn_factory, clock):
    v1 = profile(conn, version="1.0")
    a = ingest(conn, hh_item(), clock)
    other = [{"company": "КБ Север", "position": "Инженер-электронщик", "start": "2019-01-01", "end": None}]
    b = ingest(conn, hh_item(id="b2", experience=other, area={"id": "2", "name": "Новороссийск"}), clock)
    assert b.person_id != a.person_id
    db.run(conn, "UPDATE person SET watch = 'dropped' WHERE id = %s", (b.person_id,))
    v2 = profile(conn, version="1.1", keywords=["Altium", "OrCAD", "KiCad", "Eagle", "P-CAD", "Proteus"])
    stats = rescore_profile(conn_factory, v2["id"], now=clock.now())
    assert stats.seen >= 2
    for pid in (a.person_id, b.person_id):
        m = db.one(conn, "SELECT * FROM match WHERE person_id = %s AND role_profile_id = %s", (pid, v2["id"]))
        assert m and m["profile_version"] == "1.1"
    old = db.one(conn, "SELECT score FROM match WHERE person_id = %s AND role_profile_id = %s", (a.person_id, v1["id"]))
    assert old, "оценка по прежней версии остаётся историей"


def test_s20_observation_below_threshold_gets_card_under_new_version(conn, conn_factory, clock):
    profile(conn, version="1.0", title="Инженер ОТК", keywords=["AQL", "8D", "ПСИ"], donor_companies=["ОТК-Пример"],
            salary_max=90000)
    r = ingest(conn, hh_item(), clock)
    assert r.person_id is None
    v2 = profile(conn, version="1.1", title="Ведущий инженер-электронщик")
    stats = rescore_profile(conn_factory, v2["id"], now=clock.now())
    pid = db.val(conn, "SELECT person_id FROM observation WHERE id = %s", (r.observation_id,))
    assert pid and stats.persons == 1
    assert [e["kind"] for e in db.rows(conn, "SELECT kind FROM obs_event WHERE person_id = %s", (pid,))] == ["new"]


def test_s20_unapproved_version_is_refused(conn, conn_factory, clock):
    draft = profile(conn, approved=False)
    with pytest.raises(ProfileNotApproved):
        rescore_profile(conn_factory, draft["id"], now=clock.now())


def test_s20_expired_observation_not_rechecked(conn, conn_factory, clock):
    profile(conn, version="1.0", title="Инженер ОТК", keywords=["AQL"], donor_companies=["ОТК-Пример"],
            salary_max=90000)
    r = ingest(conn, hh_item(), clock)
    db.run(conn, "UPDATE observation SET retention_until = %s::date - 1 WHERE id = %s",
           (clock.now(), r.observation_id))
    v2 = profile(conn, version="1.1")
    rescore_profile(conn_factory, v2["id"], now=clock.now())
    assert db.val(conn, "SELECT person_id FROM observation WHERE id = %s", (r.observation_id,)) is None
