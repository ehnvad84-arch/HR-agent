"""T9–T10 funnel-report: API библиотеки этапов и наборов (S1–S5), движения кандидата и отчётов (S6–S9, S11–S14, S16);
права и граница видимости (AD-7)."""
from datetime import timedelta

import pytest
from factories import own_unit, profile, user

from hr import auth, db, stages
from hr.main import app


@pytest.fixture
def prof(conn):
    return profile(conn)


@pytest.fixture
def boss(conn):
    return user(conn, "boss", "hiring_manager")


def new_vacancy(c, h, prof, boss):
    r = c.post("/api/vacancy", json={"role_profile_id": prof["id"], "title": prof["title"], "hiring_manager_id": boss},
               headers=h)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def sid(conn, title):
    return db.val(conn, "SELECT id FROM ops.stage WHERE title = %s", (title,))


def items(sets):
    return [{"slot": i["slot"], "stage_id": i["stage_id"], "label": i["label"]} for i in sets["current"]["items"]]


def four_meetings(conn, sets):
    """S3: вместо одного «Собеседования» — четыре с разными руководителями."""
    out = []
    for i in items(sets):
        out.append(i)
        if i["slot"] == 5:
            out += [{"stage_id": sid(conn, "Собеседование с руководителем"), "label": f"Руководитель {n}"}
                    for n in (2, 3, 4)]
    return out


def listed(c, vid):
    return next(v for v in c.get("/api/vacancies").json()["open"] if v["id"] == vid)


# ------------------------------------------------------------------ S1: библиотека

def test_s1_library_read_and_head_adds_stage(conn, api):
    r, _, _ = api("recruiter")
    lib = r.get("/api/stages").json()
    assert [x["code"] for x in lib["rows"]] == ["selected", "contact", "screening", "interview", "meeting",
                                                "security", "offer", "first_day"]
    assert {s["title"] for s in lib["stages"]} >= {"Тестовое задание", "Собеседование с руководителем"}
    assert lib["reasons"][-1]["code"] == "other"

    head, hh, _ = api("head_recruiter")
    made = head.post("/api/stages", json={"title": "Собеседование с директором", "report_row": "meeting"}, headers=hh)
    assert made.status_code == 200 and made.json()["id"] == sid(conn, "Собеседование с директором")
    dup = head.post("/api/stages", json={"title": "собеседование С ДИРЕКТОРОМ", "report_row": "meeting"}, headers=hh)
    assert dup.status_code == 409 and dup.json()["error"] == "duplicate"
    bad = head.post("/api/stages", json={"title": "Звонок", "report_row": "nope"}, headers=hh)
    assert bad.status_code == 422 and bad.json()["error"] == "validation"


def test_s7_other_min_in_library_from_settings(conn, api):
    """Порог комментария «Другое» окна берут из справочника: настройку поменяли — окно и сервер не расходятся."""
    r, _, _ = api("recruiter")
    assert r.get("/api/stages").json()["other_min"] == 100
    db.run(conn, "UPDATE settings SET value = '150' WHERE key = 'stage_fail_other_min'")
    assert r.get("/api/stages").json()["other_min"] == 150


def test_s1_retire_stage(conn, api):
    head, hh, _ = api("head_recruiter")
    extra = sid(conn, "Тестовое задание")
    r = head.post(f"/api/stages/{extra}/retire", json={}, headers=hh)
    assert r.status_code == 200 and r.json() == {"id": extra}
    assert db.val(conn, "SELECT is_active FROM ops.stage WHERE id = %s", (extra,)) is False
    again = head.post(f"/api/stages/{extra}/retire", json={}, headers=hh)
    assert again.status_code == 409 and again.json()["error"] == "retired"
    std = head.post(f"/api/stages/{sid(conn, 'Оффер')}/retire", json={}, headers=hh)
    assert std.status_code == 409 and std.json()["error"] == "standard"
    assert head.post("/api/stages/999999/retire", json={}, headers=hh).status_code == 404


def test_s1_library_write_needs_stage_approve(conn, api):
    for role in ("recruiter", "hiring_manager", "agent"):
        c, h, _ = api(role)
        assert c.post("/api/stages", json={"title": "Звонок", "report_row": "contact"}, headers=h).status_code == 403
        assert c.post(f"/api/stages/{sid(conn, 'Тестовое задание')}/retire", json={}, headers=h).status_code == 403


# ------------------------------------------------------------------ S2–S4: набор вакансии

def test_s2_new_vacancy_has_standard_set(conn, api, prof, boss):
    r, h, _ = api("recruiter")
    vid = new_vacancy(r, h, prof, boss)
    sets = r.get(f"/api/vacancy/{vid}/stage-sets").json()
    assert [i["slot"] for i in sets["current"]["items"]] == list(range(1, 9))
    assert sets["current"]["people"] == {} and sets["proposed"] is None and sets["declined"] is None
    v = listed(r, vid)
    assert v["stages_pending"] is False and v["stages_declined"] is False


def test_s3_propose_pending_and_withdraw(conn, api, prof, boss):
    r, h, _ = api("recruiter")
    vid = new_vacancy(r, h, prof, boss)
    body = {"items": four_meetings(conn, r.get(f"/api/vacancy/{vid}/stage-sets").json()), "note": "4 руководителя"}
    p = r.post(f"/api/vacancy/{vid}/stage-sets", json=body, headers=h)
    assert p.status_code == 200 and p.json()["version"] == 2
    sets = r.get(f"/api/vacancy/{vid}/stage-sets").json()
    assert len(sets["current"]["items"]) == 8 and len(sets["proposed"]["items"]) == 11
    assert sets["proposed"]["note"] == "4 руководителя"
    assert listed(r, vid)["stages_pending"] is True
    again = r.post(f"/api/vacancy/{vid}/stage-sets", json=body, headers=h)
    assert again.status_code == 409 and again.json()["error"] == "pending_exists"
    w = r.post(f"/api/stage-sets/{p.json()['id']}/withdraw", json={}, headers=h)
    assert w.status_code == 200 and w.json() == {"state": "withdrawn"}
    assert listed(r, vid)["stages_pending"] is False
    twice = r.post(f"/api/stage-sets/{p.json()['id']}/withdraw", json={}, headers=h)
    assert twice.status_code == 409 and twice.json()["error"] == "not_proposed"
    assert r.post("/api/stage-sets/999999/withdraw", json={}, headers=h).status_code == 404


def test_s4_head_approves(conn, api, prof, boss):
    r, h, _ = api("recruiter")
    vid = new_vacancy(r, h, prof, boss)
    p = r.post(f"/api/vacancy/{vid}/stage-sets",
               json={"items": four_meetings(conn, r.get(f"/api/vacancy/{vid}/stage-sets").json())}, headers=h).json()
    assert r.post(f"/api/stage-sets/{p['id']}/approve", json={}, headers=h).status_code == 403
    head, hh, hid = api("head_recruiter")
    a = head.post(f"/api/stage-sets/{p['id']}/approve", json={}, headers=hh)
    assert a.status_code == 200 and a.json() == {"state": "approved"}
    sets = r.get(f"/api/vacancy/{vid}/stage-sets").json()
    assert sets["current"]["version"] == 2 and len(sets["current"]["items"]) == 11 and sets["proposed"] is None
    assert db.val(conn, "SELECT count(*) FROM agent_change WHERE kind = 'stages' AND applied_by = %s", (hid,)) == 1


def test_s4_head_declines_with_comment_recruiter_sees_it(conn, api, prof, boss):
    r, h, _ = api("recruiter")
    vid = new_vacancy(r, h, prof, boss)
    p = r.post(f"/api/vacancy/{vid}/stage-sets",
               json={"items": four_meetings(conn, r.get(f"/api/vacancy/{vid}/stage-sets").json())}, headers=h).json()
    head, hh, _ = api("head_recruiter")
    empty = head.post(f"/api/stage-sets/{p['id']}/decline", json={"comment": "  "}, headers=hh)
    assert empty.status_code == 422
    assert r.post(f"/api/stage-sets/{p['id']}/decline", json={"comment": "нет"}, headers=h).status_code == 403
    d = head.post(f"/api/stage-sets/{p['id']}/decline", json={"comment": "Хватит двух собеседований"}, headers=hh)
    assert d.status_code == 200 and d.json() == {"state": "declined"}
    sets = r.get(f"/api/vacancy/{vid}/stage-sets").json()
    assert sets["declined"]["decision_comment"] == "Хватит двух собеседований"
    assert sets["declined"]["decided_by_name"] == "Проверочный head_recruiter"
    assert len(sets["current"]["items"]) == 8
    v = listed(r, vid)
    assert v["stages_declined"] is True and v["stages_pending"] is False
    late = head.post(f"/api/stage-sets/{p['id']}/approve", json={}, headers=hh)
    assert late.status_code == 409 and late.json()["error"] == "not_proposed"


# ------------------------------------------------------------------ S5: этап с людьми не убрать

def test_s5_busy_stage_and_fixed_rows(conn, api, prof, boss):
    r, h, _ = api("recruiter")
    vid = new_vacancy(r, h, prof, boss)
    st = db.one(conn, "SELECT id FROM ops.stage WHERE report_row = 'security' AND is_standard")
    cid = db.val(conn, """INSERT INTO candidate (fio, retention_until) VALUES ('Вымышленный В. В.', current_date + 100)
                          RETURNING id""")
    db.run(conn, """INSERT INTO ops.candidate_stage (candidate_id, vacancy_id, slot, stage_id, report_row)
                    VALUES (%s, %s, 6, %s, 'security')""", (cid, vid, st["id"]))
    cur = items(r.get(f"/api/vacancy/{vid}/stage-sets").json())
    busy = r.post(f"/api/vacancy/{vid}/stage-sets", json={"items": [i for i in cur if i["slot"] != 6]}, headers=h)
    assert busy.status_code == 409
    assert busy.json()["error"] == "stage_busy" and busy.json()["slot"] == 6 and busy.json()["count"] == 1
    assert "сначала переведите" in busy.json()["message"]
    fixed = r.post(f"/api/vacancy/{vid}/stage-sets", json={"items": [i for i in cur if i["slot"] != 8]}, headers=h)
    assert fixed.status_code == 422 and fixed.json()["error"] == "validation"
    assert r.post("/api/vacancy/999999/stage-sets", json={"items": cur}, headers=h).status_code == 404


# ------------------------------------------------------------------ права и граница видимости

def test_sets_scope_manager_sees_own_vacancy_only(conn, api, prof, boss):
    r, h, _ = api("recruiter")
    other = new_vacancy(r, h, prof, boss)
    m, mh, mid = api("hiring_manager", login="rnd_head")
    own_unit(conn, mid, prof["code"])
    mine = new_vacancy(r, h, prof, mid)
    assert m.get(f"/api/vacancy/{mine}/stage-sets").status_code == 200
    assert m.get(f"/api/vacancy/{other}/stage-sets").status_code == 404
    assert m.get("/api/vacancy/999999/stage-sets").status_code == 404
    assert m.get("/api/stages").status_code == 200
    body = {"items": four_meetings(conn, r.get(f"/api/vacancy/{mine}/stage-sets").json())}
    assert m.post(f"/api/vacancy/{mine}/stage-sets", json=body, headers=mh).status_code == 403


def test_agent_writes_no_stages(conn, api, prof, boss):
    r, h, _ = api("recruiter")
    vid = new_vacancy(r, h, prof, boss)
    body = {"items": four_meetings(conn, r.get(f"/api/vacancy/{vid}/stage-sets").json())}
    p = r.post(f"/api/vacancy/{vid}/stage-sets", json=body, headers=h).json()
    a, ah, _ = api("agent")
    assert a.post(f"/api/vacancy/{vid}/stage-sets", json=body, headers=ah).status_code == 403
    for act in ("withdraw", "approve"):
        assert a.post(f"/api/stage-sets/{p['id']}/{act}", json={}, headers=ah).status_code == 403
    assert a.post(f"/api/stage-sets/{p['id']}/decline", json={"comment": "x"}, headers=ah).status_code == 403


def test_stage_routes_scoped_and_with_permission():
    assert auth.routes_unscoped(app) == []
    assert not [r for r in auth.routes_without_permission(app) if "stage" in r]
    paths = {p for p, _, _ in auth.api_routes(app)}
    assert {"/api/stages", "/api/stages/{stage_id}/retire", "/api/vacancy/{vacancy_id}/stage-sets",
            "/api/stage-sets/{set_id}/withdraw", "/api/stage-sets/{set_id}/approve",
            "/api/stage-sets/{set_id}/decline"} <= paths


# ------------------------------------------------------------------ T10: движение кандидата (S6–S9, S14, S16)

OTHER_100 = "Кандидат попросил отложить выход на два месяца из-за переезда семьи, ждать вакансия не может, " \
            "решили завершить сейчас."
CLOSE_200 = "Вакансия закрыта: кандидат вышел на работу в первый день, все этапы пройдены, руководитель доволен " \
            "выбором, дополнительный поиск не нужен, остальным кандидатам сообщим об итогах и сохраним их в резерве " \
            "на будущее."


def taken(conn, clock, vid, uid):
    """Вымышленный кандидат, взятый в работу: открыт первый этап набора («Отобранные резюме»)."""
    cid = db.val(conn, """INSERT INTO candidate (fio, retention_until) VALUES ('Вымышленный В. В.', current_date + 100)
                          RETURNING id""")
    stages.enter(conn, cid, vid, uid, clock.now())
    return cid


def open_slot(conn, cid, vid):
    return db.val(conn, """SELECT slot FROM ops.candidate_stage WHERE candidate_id = %s AND vacancy_id = %s
                           AND outcome IS NULL AND cancelled_at IS NULL""", (cid, vid))


def mark(c, h, vid, cid, **body):
    return c.post(f"/api/vacancy/{vid}/candidate/{cid}/mark", json=body, headers=h)


def test_s6_mark_passed_moves_forward_only(conn, api, prof, boss, clock):
    r, h, uid = api("recruiter")
    vid = new_vacancy(r, h, prof, boss)
    cid = taken(conn, clock, vid, uid)
    ok = mark(r, h, vid, cid, slot=1, outcome="passed")
    assert ok.status_code == 200 and ok.json() == {"next_slot": 2, "offer_close": False}
    back = mark(r, h, vid, cid, slot=1, outcome="passed")
    assert back.status_code == 409 and back.json()["error"] == "stale"
    skip = mark(r, h, vid, cid, slot=3, outcome="passed")
    assert skip.status_code == 409 and skip.json()["error"] == "stale"
    assert open_slot(conn, cid, vid) == 2
    assert mark(r, h, 999999, cid, slot=2, outcome="passed").status_code == 404
    stranger = db.val(conn, """INSERT INTO candidate (fio, retention_until) VALUES ('Другой Д. Д.', current_date + 1)
                               RETURNING id""")
    assert mark(r, h, vid, stranger, slot=1, outcome="passed").status_code == 404


def test_s36_mark_passed_with_meeting_date(conn, api, prof, boss, clock):
    """T12: «Прошёл» с датой встречи — встреча попадает в «Сегодня и завтра»; при отказе дата не нужна (422)."""
    r, h, uid = api("recruiter")
    vid = new_vacancy(r, h, prof, boss)
    cid = taken(conn, clock, vid, uid)
    when = (clock.now() + timedelta(hours=3)).isoformat()
    bad = mark(r, h, vid, cid, slot=1, outcome="refused", reason="conditions", scheduled_at=when)
    assert bad.status_code == 422 and bad.json()["error"] == "validation"
    assert open_slot(conn, cid, vid) == 1
    ok = mark(r, h, vid, cid, slot=1, outcome="passed", scheduled_at=when)
    assert ok.status_code == 200 and ok.json()["next_slot"] == 2
    assert db.one(conn, "SELECT kind, vacancy_id FROM interview WHERE candidate_id = %s", (cid,)) == \
        {"kind": "phone", "vacancy_id": vid}
    assert len(r.get("/api/today").json()["meetings"]) == 1


def test_s7_failed_and_refused_need_reason(conn, api, prof, boss, clock):
    r, h, uid = api("recruiter")
    vid = new_vacancy(r, h, prof, boss)
    cid = taken(conn, clock, vid, uid)
    for body in ({"outcome": "failed"}, {"outcome": "failed", "reason": "no_show"},
                 {"outcome": "refused", "reason": "offer_declined"},
                 {"outcome": "refused", "reason": "other", "comment": "коротко"}, {"outcome": "maybe"}):
        bad = mark(r, h, vid, cid, slot=1, **body)
        assert bad.status_code == 422 and bad.json()["error"] == "validation", body
    assert open_slot(conn, cid, vid) == 1
    ok = mark(r, h, vid, cid, slot=1, outcome="refused", reason="other", comment=OTHER_100)
    assert ok.status_code == 200 and ok.json() == {"next_slot": None, "offer_close": False}
    other = taken(conn, clock, vid, uid)
    assert mark(r, h, vid, other, slot=1, outcome="failed", reason="skills").status_code == 200
    assert db.val(conn, "SELECT status::text FROM candidate WHERE id = %s", (other,)) == "rejected_by_us"


def test_s8_s9_first_day_offer_close_then_closed_vacancy(conn, api, prof, boss, clock):
    r, h, uid = api("recruiter")
    vid = new_vacancy(r, h, prof, boss)
    cid, late = taken(conn, clock, vid, uid), taken(conn, clock, vid, uid)
    for _ in range(7):                         # до «Пришедших в 1 день»; «Собеседование» — резолюция руководителя
        assert mark(r, h, vid, cid, slot=open_slot(conn, cid, vid), outcome="passed").status_code == 200
    out = mark(r, h, vid, cid, slot=8, outcome="passed")
    assert out.status_code == 200 and out.json() == {"next_slot": None, "offer_close": True}
    assert r.post(f"/api/vacancy/{vid}/close", json={"comment": CLOSE_200}, headers=h).status_code == 200
    closed = mark(r, h, vid, late, slot=1, outcome="passed")
    assert closed.status_code == 409 and closed.json()["error"] == "vacancy_closed"
    assert mark(r, h, vid, late, slot=1, outcome="refused", reason="conditions").status_code == 200


def test_s16_undo_own_today_else_head(conn, api, prof, boss, clock):
    r, h, uid = api("recruiter")
    vid = new_vacancy(r, h, prof, boss)
    cid = taken(conn, clock, vid, uid)
    url = f"/api/vacancy/{vid}/candidate/{cid}/undo"
    none = r.post(url, json={"comment": "ошиблась"}, headers=h)
    assert none.status_code == 409 and none.json()["error"] == "nothing_to_undo"
    assert mark(r, h, vid, cid, slot=1, outcome="passed").status_code == 200
    assert r.post(url, json={"comment": "  "}, headers=h).status_code == 422
    u = r.post(url, json={"comment": "ошиблась кандидатом"}, headers=h)
    assert u.status_code == 200 and u.json() == {"slot": 1}
    assert open_slot(conn, cid, vid) == 1

    assert mark(r, h, vid, cid, slot=1, outcome="passed").status_code == 200
    r2, h2, _ = api("recruiter", login="rec2")
    alien = r2.post(url, json={"comment": "не моя"}, headers=h2)
    assert alien.status_code == 403
    assert alien.json() == {"error": "undo_expired", "message": "Отменить может руководитель ОП"}
    clock.advance(days=1)
    r, h, _ = api("recruiter")                 # сессия за сутки истекла — тот же рекрутер входит заново
    stale = r.post(url, json={"comment": "вчерашняя"}, headers=h)
    assert stale.status_code == 403 and stale.json()["error"] == "undo_expired"
    head, hh, _ = api("head_recruiter")
    assert head.post(url, json={"comment": "отменяю по просьбе"}, headers=hh).json() == {"slot": 1}
    assert db.val(conn, """SELECT count(*) FROM audit_log WHERE object_type = 'candidate_stage'
                           AND detail->>'undo' = 'true'""") == 2
    assert r.post(f"/api/vacancy/999999/candidate/{cid}/undo", json={"comment": "x"}, headers=h).status_code == 404


def test_s14_pause_resume_api(conn, api, prof, boss, clock):
    r, h, uid = api("recruiter")
    vid = new_vacancy(r, h, prof, boss)
    cid = taken(conn, clock, vid, uid)
    p = r.post(f"/api/vacancy/{vid}/pause", json={"comment": "руководитель в отпуске"}, headers=h)
    assert p.status_code == 200 and p.json() == {"state": "on_hold"}
    twice = r.post(f"/api/vacancy/{vid}/pause", json={}, headers=h)
    assert twice.status_code == 409 and twice.json()["error"] == "conflict"
    assert mark(r, h, vid, cid, slot=1, outcome="passed").status_code == 200       # на паузе отмечать можно
    assert db.val(conn, "SELECT comment FROM ops.vacancy_pause WHERE vacancy_id = %s", (vid,)) == \
        "руководитель в отпуске"
    res = r.post(f"/api/vacancy/{vid}/resume", headers=h)
    assert res.status_code == 200 and res.json() == {"state": "open"}
    assert r.post(f"/api/vacancy/{vid}/resume", headers=h).status_code == 409
    assert r.post("/api/vacancy/999999/pause", headers=h).status_code == 404
    assert db.val(conn, """SELECT count(*) FROM audit_log WHERE object_type = 'vacancy' AND object_id = %s
                           AND (detail ? 'pause' OR detail ? 'resume')""", (vid,)) == 2


def test_movement_needs_rights(conn, api, prof, boss, clock):
    r, h, uid = api("recruiter")
    vid = new_vacancy(r, h, prof, boss)
    cid = taken(conn, clock, vid, uid)
    assert mark(r, {}, vid, cid, slot=1, outcome="passed").status_code == 403       # без CSRF
    for role in ("hiring_manager", "director", "agent"):
        c, ch, _ = api(role)
        assert mark(c, ch, vid, cid, slot=1, outcome="passed").status_code == 403, role
        assert c.post(f"/api/vacancy/{vid}/candidate/{cid}/undo", json={"comment": "x"},
                      headers=ch).status_code == 403, role
        assert c.post(f"/api/vacancy/{vid}/pause", headers=ch).status_code == 403, role
    assert open_slot(conn, cid, vid) == 1


# ------------------------------------------------------------------ T10: отчёты (S11–S13)

def test_s11_vacancy_report_own_or_department(conn, api, prof, boss, clock):
    r, h, uid = api("recruiter")
    mine = new_vacancy(r, h, prof, boss)
    cid = taken(conn, clock, mine, uid)
    assert mark(r, h, mine, cid, slot=1, outcome="passed").status_code == 200
    rep = r.get(f"/api/vacancy/{mine}/funnel-report")
    assert rep.status_code == 200
    body = rep.json()
    assert {"opened_at", "closed_at", "since", "days", "paused_days", "carried", "rows", "conversion"} <= body.keys()
    assert [x["code"] for x in body["rows"]][:2] == ["selected", "contact"] and len(body["rows"]) == 8
    assert (body["rows"][0]["entered"], body["rows"][0]["passed"], body["rows"][0]["pct"]) == (1, 1, 100)
    assert {"entered", "passed", "pct", "in_set", "stages"} <= body["rows"][0].keys()
    assert body["conversion"] == {"hired": 0, "selected": 1, "pct": 0}

    r2, h2, _ = api("recruiter", login="rec2")
    alien = new_vacancy(r2, h2, prof, boss)
    assert r.get(f"/api/vacancy/{alien}/funnel-report").status_code == 404
    assert r.get("/api/vacancy/999999/funnel-report").status_code == 404
    head, _, _ = api("head_recruiter")
    assert head.get(f"/api/vacancy/{alien}/funnel-report").status_code == 200
    m, _, mid = api("hiring_manager", login="rnd_head")
    own_unit(conn, mid, prof["code"])
    managed = new_vacancy(r, h, prof, mid)
    assert m.get(f"/api/vacancy/{managed}/funnel-report").status_code == 200
    assert m.get(f"/api/vacancy/{mine}/funnel-report").status_code == 404
    d, _, _ = api("director")
    assert d.get(f"/api/vacancy/{mine}/funnel-report").status_code == 404           # подразделений нет — не видно


def test_s12_recruiter_report_self_or_head(conn, api, prof, boss, clock):
    r, h, uid = api("recruiter")
    mine = new_vacancy(r, h, prof, boss)
    r2, h2, uid2 = api("recruiter", login="rec2")
    new_vacancy(r2, h2, prof, boss)
    own = r.get(f"/api/funnel/recruiter/{uid}")
    assert own.status_code == 200
    body = own.json()
    assert body["year"] == clock.now().year and clock.now().year in body["years"]
    assert [v["id"] for v in body["vacancies"]] == [mine] and len(body["total"]["rows"]) == 8
    assert "conversion" in body["total"]
    empty = r.get(f"/api/funnel/recruiter/{uid}?year=2020").json()
    assert empty["year"] == 2020 and empty["vacancies"] == []
    assert r.get(f"/api/funnel/recruiter/{uid2}").status_code == 404
    head, _, _ = api("head_recruiter")
    assert [v["recruiter_id"] for v in head.get(f"/api/funnel/recruiter/{uid2}").json()["vacancies"]] == [uid2]
    assert head.get("/api/funnel/recruiter/999999").status_code == 404
    for role in ("director", "hiring_manager", "agent"):
        c, _, _ = api(role)
        assert c.get(f"/api/funnel/recruiter/{uid}").status_code == 404, role


def test_s13_department_report_head_only(conn, api, prof, boss, clock):
    r, h, uid = api("recruiter")
    new_vacancy(r, h, prof, boss)
    head, _, _ = api("head_recruiter")
    dep = head.get(f"/api/funnel/department?year={clock.now().year}")
    assert dep.status_code == 200
    body = dep.json()
    assert [x["id"] for x in body["recruiters"]] == [uid] and body["recruiters"][0]["vacancies"] == 1
    assert len(body["total"]["rows"]) == 8 and body["years"]
    for role in ("recruiter", "director", "hiring_manager", "agent"):
        c, _, _ = api(role, login=f"{role}_dep")
        assert c.get("/api/funnel/department").status_code == 404, role


def test_movement_and_report_routes_locked():
    assert auth.routes_unscoped(app) == []
    paths = {p for p, _, _ in auth.api_routes(app)}
    assert {"/api/vacancy/{vacancy_id}/candidate/{candidate_id}/mark",
            "/api/vacancy/{vacancy_id}/candidate/{candidate_id}/undo", "/api/vacancy/{vacancy_id}/pause",
            "/api/vacancy/{vacancy_id}/resume", "/api/vacancy/{vacancy_id}/funnel-report",
            "/api/funnel/recruiter/{user_id}", "/api/funnel/department"} <= paths
    assert not [r for r in auth.routes_without_permission(app) if "funnel" in r or "candidate/" in r]


def test_text_lengths_limited_422(conn, api, prof, boss):
    """Названия этапов и подписи — до 80 знаков, комментарии — до 2000: длиннее — 422 validation."""
    head, hh, _ = api("head_recruiter")
    vid = new_vacancy(head, hh, prof, boss)
    sets = head.get(f"/api/vacancy/{vid}/stage-sets").json()
    long80, long2000 = "э" * 81, "к" * 2001
    with_label = items(sets) + [{"stage_id": sid(conn, "Тестовое задание"), "label": long80}]
    with_label.sort(key=lambda i: i.get("slot") or 4.5)
    calls = [
        ("/api/stages", {"title": long80, "report_row": "meeting"}),
        (f"/api/vacancy/{vid}/stage-sets", {"items": with_label}),
        (f"/api/vacancy/{vid}/stage-sets", {"items": items(sets), "note": long2000}),
        ("/api/stage-sets/1/decline", {"comment": long2000}),
        (f"/api/vacancy/{vid}/candidate/1/mark", {"slot": 1, "outcome": "failed", "reason": "skills",
                                                  "comment": long2000}),
        (f"/api/vacancy/{vid}/candidate/1/undo", {"comment": long2000}),
        (f"/api/vacancy/{vid}/pause", {"comment": long2000}),
    ]
    for url, body in calls:
        r = head.post(url, json=body, headers=hh)
        assert r.status_code == 422 and r.json()["error"] == "validation", (url, r.status_code)
    ok = head.post("/api/stages", json={"title": "э" * 80, "report_row": "meeting"}, headers=hh)
    assert ok.status_code == 200, "80 знаков — можно"
