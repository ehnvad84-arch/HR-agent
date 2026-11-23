"""T14 workplace: вакансии — список, создание, закрытие с комментарием, «Открыть заново» (S21, S23, S38; 23, 24)."""
from datetime import UTC, date, datetime, timedelta

import psycopg
import pytest
from factories import org, profile, user

from hr import db, stages, vacancies

LONG = ("Позиция закрыта: кандидат из резерва вышел на работу 1 ноября, испытательный срок три месяца. "
        "Профиль подтвердился, по нему стоит продолжать наблюдение: "
        "в отделе ещё одна ставка откроется весной, к марту.")
assert len(LONG) >= 200


@pytest.fixture
def prof(conn):
    return profile(conn)


BOSS: dict = {}


@pytest.fixture(autouse=True)
def boss(conn):
    """manager-cabinet S12: заказчик обязателен — у каждой вакансии теста он есть."""
    BOSS["id"] = user(conn, "boss", "hiring_manager")
    return BOSS["id"]


def create(c, h, prof, **kw):
    kw.setdefault("hiring_manager_id", BOSS["id"])
    return c.post("/api/vacancy", json={"role_profile_id": prof["id"], "title": prof["title"], **kw}, headers=h)


def test_s21_create_and_list(conn, api, prof):
    c, h, uid = api()
    r = create(c, h, prof, headcount=2, target_date="2026-12-31")
    assert r.status_code == 200
    v = db.one(conn, "SELECT origin::text AS origin, headcount, recruiter_id, state FROM vacancy WHERE id = %s",
               (r.json()["id"],))
    assert v == {"origin": "recruiter", "headcount": 2, "recruiter_id": uid, "state": "open"}
    lst = c.get("/api/vacancies").json()
    assert lst["open"][0]["id"] == r.json()["id"] and lst["open"][0]["in_work"] == 0 and lst["closed"] == []


def test_unapproved_profile_rejected(conn, api):
    draft = profile(conn, code="draft_p", approved=False)
    c, h, _ = api()
    assert create(c, h, draft).status_code == 422


def test_recruiter_can_hiring_manager_cannot(conn, api, prof):
    c, h, _ = api("hiring_manager")
    assert create(c, h, prof).status_code == 403


def test_s23_close_requires_200_chars_and_keeps_sourcing(conn, api, prof):
    c, h, uid = api()
    vid = create(c, h, prof).json()["id"]
    r = c.post(f"/api/vacancy/{vid}/close", json={"comment": "закрыли"}, headers=h)
    assert r.status_code == 422 and "200" in r.json()["message"]
    with pytest.raises(psycopg.errors.CheckViolation):
        with conn.transaction():
            db.run(conn, "UPDATE vacancy SET state = 'closed', close_comment = 'коротко' WHERE id = %s", (vid,))
    r = c.post(f"/api/vacancy/{vid}/close", json={"comment": LONG}, headers=h)
    assert r.json()["state"] == "closed"
    v = db.one(conn, "SELECT state, keep_sourcing, closed_by, closed_at FROM vacancy WHERE id = %s", (vid,))
    assert v["keep_sourcing"] is True and v["closed_by"] == uid and v["closed_at"]
    assert c.post(f"/api/vacancy/{vid}/close", json={"comment": LONG}, headers=h).status_code == 409
    assert c.get("/api/vacancies").json()["closed"][0]["id"] == vid


def test_s38_reopen(conn, api, prof):
    c, h, uid = api()
    vid = create(c, h, prof).json()["id"]
    c.post(f"/api/vacancy/{vid}/close", json={"comment": LONG}, headers=h)
    r = c.post(f"/api/vacancy/{vid}/reopen", json={}, headers=h)
    assert r.json()["state"] == "open"
    assert db.val(conn, "SELECT state FROM vacancy WHERE id = %s", (vid,)) == "open"
    a = db.one(conn, "SELECT action, object_type FROM audit_log WHERE object_id = %s AND user_id = %s", (vid, uid))
    assert a == {"action": "edit", "object_type": "vacancy"}
    assert c.post(f"/api/vacancy/{vid}/reopen", json={}, headers=h).status_code == 409


def test_s12_responsible_and_customer(conn, api, clock):
    o = org(conn, clock.now())
    c, h, uid = api("recruiter")
    head = user(conn, "sales_head", "hiring_manager", o["units"]["u-sales"])
    assert c.get(f"/api/vacancy/defaults?role_profile_id={o['sales']['id']}").json()["hiring_manager_id"] == head
    r = c.post("/api/vacancy", json={"role_profile_id": o["sales"]["id"], "title": "МПП"}, headers=h)
    v = db.one(conn, "SELECT recruiter_id, hiring_manager_id FROM vacancy WHERE id = %s", (r.json()["id"],))
    assert v == {"recruiter_id": uid, "hiring_manager_id": head}
    nobody = c.post("/api/vacancy", json={"role_profile_id": o["eng"]["id"], "title": "Инж"}, headers=h)
    assert nobody.status_code == 422 and "заказчик" in nobody.json()["message"]
    bad = c.post("/api/vacancy", json={"role_profile_id": o["sales"]["id"], "title": "МПП", "recruiter_id": head},
                 headers=h)
    assert bad.status_code == 422
    assert {u["id"] for u in c.get("/api/staff?kind=managers").json()["items"]} >= {head}
    lst = c.get("/api/vacancies").json()["open"]
    assert any(x["hiring_manager"] == "Проверочный sales_head" for x in lst)


def test_s13_collect_rights(api):
    m, _, _ = api("hiring_manager")
    assert m.get("/api/hh/limits").status_code == 403
    r, _, _ = api("recruiter")
    assert r.get("/api/hh/limits").status_code != 403


# ------------------------------------------------------------------ funnel-report T7, S14: пауза вакансии

D0 = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)


def at(days, hour=9):
    return D0 + timedelta(days=days, hours=hour - 9)


@pytest.fixture
def vac(conn, prof):
    rec = user(conn, "rec", "recruiter")
    return {"id": vacancies.create(conn, prof["id"], prof["title"], rec, hiring_manager_id=BOSS["id"]), "rec": rec}


def pauses(conn, vid):
    return db.rows(conn, """SELECT kind, from_date, to_date, by FROM ops.vacancy_pause
                            WHERE vacancy_id = %s ORDER BY id""", (vid,))


def state(conn, vid):
    return db.val(conn, "SELECT state FROM vacancy WHERE id = %s", (vid,))


def test_s14_pause_and_resume_write_pause_days(conn, vac):
    vid, rec = vac["id"], vac["rec"]
    assert vacancies.pause(conn, vid, rec, at(2)) == "on_hold"
    assert state(conn, vid) == "on_hold"
    assert pauses(conn, vid) == [{"kind": "pause", "from_date": date(2026, 11, 5), "to_date": None, "by": rec}]
    with pytest.raises(vacancies.Conflict):
        vacancies.pause(conn, vid, rec, at(3))
    assert vacancies.resume(conn, vid, rec, at(8)) == "open"
    assert state(conn, vid) == "open"
    assert pauses(conn, vid) == [{"kind": "pause", "from_date": date(2026, 11, 5), "to_date": date(2026, 11, 11),
                                  "by": rec}]
    with pytest.raises(vacancies.Conflict):
        vacancies.resume(conn, vid, rec, at(9))
    vacancies.pause(conn, vid, rec, at(10))                # вторая пауза — новая строка
    assert [p["from_date"] for p in pauses(conn, vid)] == [date(2026, 11, 5), date(2026, 11, 13)]
    log = db.rows(conn, """SELECT action, object_type, user_id, detail FROM audit_log
                           WHERE object_type = 'vacancy' AND object_id = %s ORDER BY id""", (vid,))
    assert [entry["detail"] for entry in log] == [{"pause": True}, {"resume": True}, {"pause": True}]
    assert {(entry["action"], entry["user_id"]) for entry in log} == {("edit", rec)}


def test_s14_on_pause_no_new_people_marks_allowed(conn, vac):
    vid, rec = vac["id"], vac["rec"]
    cid = db.val(conn, """INSERT INTO candidate (fio, retention_until) VALUES ('Вымышленный В. В.', current_date + 100)
                          RETURNING id""")
    slot = stages.enter(conn, cid, vid, rec, at(0))
    vacancies.pause(conn, vid, rec, at(1))
    other = db.val(conn, """INSERT INTO candidate (fio, retention_until)
                            VALUES ('Придуманный П. П.', current_date + 100)
                            RETURNING id""")
    with pytest.raises(stages.Conflict) as e:
        stages.enter(conn, other, vid, rec, at(2))
    assert e.value.code == "vacancy_not_open"
    assert stages.mark(conn, cid, vid, slot, "passed", rec, at(2))["next_slot"] is not None


def test_s14_pause_and_resume_moscow_day(conn, vac):
    vid, rec = vac["id"], vac["rec"]
    vacancies.pause(conn, vid, rec, datetime(2026, 11, 24, 21, 30, tzinfo=UTC))   # 00:30 25.11 по Москве
    vacancies.resume(conn, vid, rec, datetime(2026, 11, 26, 20, 59, tzinfo=UTC))  # 23:59 26.11 по Москве
    assert pauses(conn, vid)[0] | {"by": None} == {"kind": "pause", "from_date": date(2026, 11, 25),
                                                   "to_date": date(2026, 11, 26), "by": None}


def test_s14_pause_only_open_vacancy(conn, vac):
    vid, rec = vac["id"], vac["rec"]
    with pytest.raises(vacancies.NotFound):
        vacancies.pause(conn, vid + 999, rec, at(1))
    with pytest.raises(vacancies.Conflict):
        vacancies.resume(conn, vid, rec, at(1))              # не на паузе
    vacancies.close(conn, vid, LONG, rec, at(1))
    with pytest.raises(vacancies.Conflict):
        vacancies.pause(conn, vid, rec, at(2))
    with pytest.raises(vacancies.Conflict):
        vacancies.resume(conn, vid, rec, at(2))
    assert pauses(conn, vid) == []


def test_s14_close_ends_pause(conn, vac):
    vid, rec = vac["id"], vac["rec"]
    vacancies.pause(conn, vid, rec, at(2))
    assert vacancies.close(conn, vid, LONG, rec, at(6)) == "closed"
    assert state(conn, vid) == "closed"
    assert pauses(conn, vid) == [{"kind": "pause", "from_date": date(2026, 11, 5), "to_date": date(2026, 11, 9),
                                  "by": rec}]


def test_s14_reopen_writes_closed_gap(conn, vac):
    vid, rec = vac["id"], vac["rec"]
    vacancies.close(conn, vid, LONG, rec, at(4))
    closed_at = db.val(conn, "SELECT closed_at FROM vacancy WHERE id = %s", (vid,))
    assert vacancies.reopen(conn, vid, rec, at(11)) == "open"
    assert pauses(conn, vid) == [{"kind": "closed_gap", "from_date": closed_at, "to_date": date(2026, 11, 14),
                                  "by": rec}]
    assert db.val(conn, "SELECT closed_at FROM vacancy WHERE id = %s", (vid,)) is None
    vacancies.pause(conn, vid, rec, at(12))                # после открытия — пауза как обычно
    assert [p["kind"] for p in pauses(conn, vid)] == ["closed_gap", "pause"]


def test_s14_reopen_cancelled_without_closed_at_no_gap(conn, vac):
    vid, rec = vac["id"], vac["rec"]
    db.run(conn, "UPDATE vacancy SET state = 'cancelled' WHERE id = %s", (vid,))
    vacancies.reopen(conn, vid, rec, at(5))
    assert pauses(conn, vid) == []


def test_in_work_counts_open_stages_of_pair(conn, prof, boss):
    """«В работе» по вакансии — люди с открытым этапом в ней: отказ в другой вакансии счёт не обнуляет."""
    rec = user(conn, "rec", "recruiter")
    now = datetime(2026, 11, 24, 9, 0, tzinfo=UTC)
    a = vacancies.create(conn, prof["id"], "A", rec, hiring_manager_id=boss)
    b = vacancies.create(conn, prof["id"], "B", rec, hiring_manager_id=boss)
    cid = db.val(conn, "INSERT INTO candidate (fio, retention_until) VALUES ('Вымышленный', current_date + 100) "
                       "RETURNING id")
    stages.enter(conn, cid, a, rec, now)
    stages.enter(conn, cid, b, rec, now)
    stages.mark(conn, cid, a, 1, "refused", rec, now, reason="conditions")     # статус — отказ, но в B он на этапе
    got = {v["title"]: v["in_work"] for v in vacancies.listing(conn)["open"]}
    assert got == {"A": 0, "B": 1}
