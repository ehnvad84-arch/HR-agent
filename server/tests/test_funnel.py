"""T10 workplace, T6 funnel-report: вход в воронку, доска по этапам набора, резерв, дата встречи (S10, S24, S36, S37).

С funnel-report этапы отмечаются окном «Итог этапа» (stages.mark); /status — только «в резерв» без открытого этапа.
"""
from datetime import timedelta

import pytest
from factories import hh_item, org, profile, source, user

from hr import db, decisions, funnel, stages, vacancies
from hr.normalize import normalize
from hr.pipeline import process

PHONE = "звонить +7 900 123-45-67 после шести"


@pytest.fixture
def cand(conn, clock):
    prof = profile(conn)
    vid = db.val(conn, "INSERT INTO vacancy (role_profile_id, title) VALUES (%s, %s) RETURNING id",
                 (prof["id"], prof["title"]))
    it = hh_item()
    pid = process(conn, source(conn, "hh"), normalize("hh", it), it, clock.now()).person_id
    uid = db.val(conn, "INSERT INTO app_user (login, full_name, role) VALUES ('t', 'Т', 'recruiter') RETURNING id")
    return decisions.take(conn, pid, uid, clock.now()), vid, pid


def status(conn, cid):
    return db.val(conn, "SELECT status::text FROM candidate WHERE id = %s", (cid,))


def marks(conn, cid):
    return db.rows(conn, """SELECT vacancy_id, slot, report_row, outcome, carried FROM ops.candidate_stage
                            WHERE candidate_id = %s AND cancelled_at IS NULL ORDER BY id""", (cid,))


def new_vacancy(conn, prof, uid):
    return vacancies.create(conn, prof["id"], "Ведущий инженер", uid, hiring_manager_id=user(conn, "boss",
                                                                                            "hiring_manager"))


# ------------------------------------------------------------------ S10: вход в воронку

def test_s10_take_puts_on_selected(conn, cand):
    cid, vid, _ = cand
    assert marks(conn, cid) == [{"vacancy_id": vid, "slot": 1, "report_row": "selected", "outcome": None,
                                 "carried": False}]
    assert status(conn, cid) == "picked_by_agent"
    assert db.val(conn, "SELECT count(*) FROM status_history WHERE candidate_id = %s", (cid,)) == 1
    assert stages.current_set(conn, vid)["version"] == 1, "вакансия v1.2.0 получила стандартный набор"


def test_s10_move_with_vacancy_enters_funnel(conn, clock):
    prof = profile(conn)
    it = hh_item()
    pid = process(conn, source(conn, "hh"), normalize("hh", it), it, clock.now()).person_id
    uid = user(conn, "rec", "recruiter")
    cid = decisions.take(conn, pid, uid, clock.now())   # открытой вакансии нет — резерв, вне воронки
    assert status(conn, cid) == "reserve" and marks(conn, cid) == []
    vid = db.val(conn, "INSERT INTO vacancy (role_profile_id, title) VALUES (%s, 'В') RETURNING id", (prof["id"],))
    funnel.move(conn, cid, "picked_by_agent", uid, clock.now(), vacancy_id=vid)
    assert [(m["vacancy_id"], m["report_row"]) for m in marks(conn, cid)] == [(vid, "selected")]
    assert db.val(conn, "SELECT count(*) FROM ops.candidate_vacancy WHERE candidate_id = %s AND vacancy_id = %s",
                  (cid, vid)) == 1


def test_s10_agent_picks_are_not_in_funnel(conn, api, clock):
    prof = profile(conn)
    c, h, uid = api("recruiter")
    vid = new_vacancy(conn, prof, uid)
    it = hh_item(last_name="Петров", first_name="Пётр")
    pid = process(conn, source(conn, "hh"), normalize("hh", it), it, clock.now()).person_id
    if not db.val(conn, "SELECT 1 FROM alert WHERE person_id = %s AND state = 'new'", (pid,)):
        db.run(conn, "INSERT INTO alert (person_id, role_profile_id, reason, headline) VALUES (%s, %s, "
                     "'new_high_score', 'x')", (pid, prof["id"]))
    db.run(conn, "UPDATE alert SET created_at = %s WHERE person_id = %s", (clock.now() - timedelta(days=2), pid))
    f = c.get(f"/api/vacancy/{vid}/funnel").json()
    assert [(p["person_id"], p["name"], p["days"]) for p in f["agent"]] == [(pid, "Петров Пётр", 2)]
    assert sum(len(s["people"]) for s in f["stages"]) == 0
    assert db.val(conn, "SELECT count(*) FROM ops.candidate_stage WHERE vacancy_id = %s", (vid,)) == 0, \
        "агент сам отметку не ставит"
    bot, bh, _ = api("agent")
    assert bot.post(f"/api/person/{pid}/take", json={"vacancy_id": vid}, headers=bh).status_code == 403
    r = c.post(f"/api/person/{pid}/take", json={"vacancy_id": vid}, headers=h)
    cid = r.json()["candidate_id"]
    f = c.get(f"/api/vacancy/{vid}/funnel").json()
    assert f["agent"] == [] and [p["candidate_id"] for p in f["stages"][0]["people"]] == [cid]


# ------------------------------------------------------------------ доска по этапам набора

def test_s24_board_columns_by_slot_with_last_mark(conn, api, clock):
    prof = profile(conn)
    c, h, uid = api("recruiter")
    head = user(conn, "head", "head_recruiter")
    vid = new_vacancy(conn, prof, uid)
    items = [{"slot": i["slot"], "stage_id": i["stage_id"]} for i in stages.current_set(conn, vid)["items"]]
    extra = db.val(conn, "SELECT id FROM ops.stage WHERE title = 'Тестовое задание'")
    items.insert(4, {"stage_id": extra, "label": "Тестовое по схемотехнике"})
    items = [i for i in items if i.get("slot") != 6]           # без «Проверки СБ»
    stages.approve(conn, stages.propose(conn, vid, items, None, uid, clock.now())["id"], head, clock.now())
    cid = db.val(conn, """INSERT INTO candidate (fio, retention_until) VALUES ('Вымышленный В. В.', current_date + 100)
                          RETURNING id""")
    stages.enter(conn, cid, vid, uid, clock.now() - timedelta(days=4))
    f = c.get(f"/api/vacancy/{vid}/funnel").json()
    assert [s["slot"] for s in f["stages"]] == [1, 2, 3, 4, 9, 5, 7, 8]
    assert f["stages"][4]["title"] == "Тестовое по схемотехнике" and f["stages"][4]["report_row"] == "interview"
    first = f["stages"][0]
    assert first["people"][0]["candidate_id"] == cid and first["avg_days"] == 4
    assert first["people"][0]["last_mark"] is None
    stages.mark(conn, cid, vid, 1, "passed", uid, clock.now())
    f = c.get(f"/api/vacancy/{vid}/funnel").json()
    [p] = f["stages"][1]["people"]
    assert p["last_mark"]["by"] == uid and p["last_mark"]["by_name"] == "Проверочный recruiter"
    assert p["last_mark"]["slot"] == 1 and p["last_mark"]["outcome"] == "passed" and p["last_mark"]["at"]
    stages.mark(conn, cid, vid, 2, "refused", uid, clock.now(), reason="no_contact")
    f = c.get(f"/api/vacancy/{vid}/funnel").json()
    assert sum(len(s["people"]) for s in f["stages"]) == 0 and f["closed"] == 1
    assert c.get("/api/vacancy/999999/funnel").status_code == 404


def test_board_without_set_shows_standard_stages(conn, api):
    prof = profile(conn)
    vid = db.val(conn, "INSERT INTO vacancy (role_profile_id, title) VALUES (%s, 'В') RETURNING id",   # как v1.2.0
                 (prof["id"],))
    c, _, _ = api("recruiter")
    f = c.get(f"/api/vacancy/{vid}/funnel").json()
    assert [s["report_row"] for s in f["stages"]] == ["selected", "contact", "screening", "interview", "meeting",
                                                     "security", "offer", "first_day"]
    assert not db.val(conn, "SELECT 1 FROM ops.stage_set WHERE vacancy_id = %s", (vid,)), "чтение ничего не пишет"


def test_manager_board_read_only_and_comments_without_contacts(conn, api, clock):
    o = org(conn, clock.now())
    rc, _, rid = api("recruiter", login="owner")
    vac = db.val(conn, """INSERT INTO vacancy (role_profile_id, title, staff_position_id, recruiter_id)
                          VALUES (%s, 'МПП', %s, %s) RETURNING id""", (o["sales"]["id"], o["pos"]["p-mpp1"], rid))
    mc, _, _ = api("hiring_manager", login="sales_head")
    user(conn, "sales_head", "hiring_manager", o["units"]["u-sales"])
    pid = db.val(conn, "INSERT INTO person (label) VALUES ('Продажи, 5 лет') RETURNING id")
    db.run(conn, "INSERT INTO match (person_id, role_profile_id, profile_version, score) VALUES (%s, %s, %s, 80)",
           (pid, o["sales"]["id"], o["sales"]["version"]))
    cid = decisions.take(conn, pid, rid, clock.now(), vac)
    stages.mark(conn, cid, vac, 1, "passed", rid, clock.now(), comment=PHONE)
    mine = mc.get(f"/api/vacancy/{vac}/funnel").json()
    # кабинет руководителя (manager-vacancies.js:97): title, people[person_id, days], avg_days
    assert all({"title", "people", "avg_days"} <= set(s) for s in mine["stages"])
    [p] = [p for s in mine["stages"] for p in s["people"]]
    assert p["person_id"] == pid and p["days"] == 0 and mine["stages"][1]["avg_days"] == 0
    assert p["notes"][0]["comment"] == "звонить [телефон] после шести"
    full = rc.get(f"/api/vacancy/{vac}/funnel").json()
    assert [n["comment"] for s in full["stages"] for x in s["people"] for n in x["notes"]] == [PHONE]


# ------------------------------------------------------------------ /status: только резерв

def test_status_only_reserve_without_open_stage(conn, api, cand, clock):
    cid, vid, _ = cand
    c, h, uid = api()
    r = c.post(f"/api/candidate/{cid}/status", json={"vacancy_id": vid, "to_status": "first_message_sent"},
               headers=h)
    assert r.status_code == 409 and r.json()["error"] == "use_stages"
    r = c.post(f"/api/candidate/{cid}/status", json={"to_status": "reserve"}, headers=h)
    assert r.status_code == 409 and r.json()["error"] == "use_stages", "открытый этап — сначала итог этапа"
    assert status(conn, cid) == "picked_by_agent"
    stages.mark(conn, cid, vid, 1, "refused", uid, clock.now(), reason="conditions")
    r = c.post(f"/api/candidate/{cid}/status", json={"to_status": "reserve", "comment": "вернуться весной"},
               headers=h)
    assert r.status_code == 200 and status(conn, cid) == "reserve"
    assert len(marks(conn, cid)) == 1, "резерв в воронку не пишет"
    assert c.post("/api/candidate/999999/status", json={"to_status": "reserve"}, headers=h).status_code == 404


def test_unknown_stage_422(conn, api, cand):
    cid, vid, _ = cand
    c, h, _ = api()
    assert c.post(f"/api/candidate/{cid}/status", json={"vacancy_id": vid, "to_status": "luna"},
                  headers=h).status_code == 422


def test_s36_meeting_date(conn, api, cand, clock):
    cid, vid, _ = cand
    c, _, uid = api()
    funnel.move(conn, cid, "phone_interview", uid, clock.now(), vacancy_id=vid,
                scheduled_at=clock.now() + timedelta(hours=3))
    i = db.one(conn, "SELECT kind, vacancy_id FROM interview WHERE candidate_id = %s", (cid,))
    assert i == {"kind": "phone", "vacancy_id": vid}
    assert len(c.get("/api/today").json()["meetings"]) == 1
    assert len(marks(conn, cid)) == 1, "пара уже в воронке — вход ничего не меняет"
