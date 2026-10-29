"""T4 workplace: «Сегодня» — сигналы, подтверждения, зависшие, встречи (S4, S5, S36, S37)."""
from datetime import timedelta

from factories import hh_item, profile, source

from hr import db
from hr.normalize import normalize
from hr.pipeline import process


def person(conn, clock, rid="a1", **kw):
    it = hh_item(id=rid, **kw)
    r = process(conn, source(conn, "hh"), normalize("hh", it), it, clock.now())
    return r.person_id or db.val(conn, "INSERT INTO person (label) VALUES ('Инженер, Краснодар') RETURNING id")


def alert(conn, pid, prof, headline="Новый в поле зрения: 88"):
    return db.val(conn, """INSERT INTO alert (person_id, role_profile_id, reason, headline)
                           VALUES (%s, %s, 'new_high_score', %s) RETURNING id""", (pid, prof["id"], headline))


def candidate(conn, pid, status="first_message_sent", silent_days=6):
    cid = db.val(conn, """INSERT INTO candidate (person_id, fio, status, retention_until, last_contact_at)
                          VALUES (%s, 'Кандидат К. К.', %s, current_date + 1000, now() - %s * interval '1 day')
                          RETURNING id""", (pid, status, silent_days))
    db.run(conn, "UPDATE person SET candidate_id = %s WHERE id = %s", (cid, pid))
    return cid


def test_s05_empty_day(api):
    c, _, _ = api()
    r = c.get("/api/today")
    assert r.status_code == 200
    assert r.json() == {"signals": [], "actions": [], "stalled": [], "meetings": []}


def test_s04_signals_with_card_and_limit(conn, clock, api):
    prof = profile(conn)
    db.run(conn, "UPDATE settings SET value = '2' WHERE key = 'daily_cards_limit'")
    pids = [person(conn, clock, rid=f"r{i}") for i in range(3)]
    for p in pids:
        alert(conn, p, prof)
    c, _, _ = api()
    sig = c.get("/api/today").json()["signals"]
    assert len(sig) == 2, "не больше daily_cards_limit"
    card = sig[0]
    assert {"alert_id", "person_id", "name", "label", "photo", "score", "headline", "role_title"} <= set(card)
    assert card["label"] and card["photo"] is False and card["headline"].startswith("Новый")


def test_name_shown_when_hh_gave_it(conn, clock, api):
    prof = profile(conn)
    alert(conn, person(conn, clock, rid="named", last_name="Петров", first_name="Пётр"), prof)
    c, _, _ = api()
    assert c.get("/api/today").json()["signals"][0]["name"] == "Петров Пётр"


def test_name_hidden_when_hh_hid_it(conn, clock, api):
    prof = profile(conn)
    alert(conn, person(conn, clock, rid="anon", last_name=None, first_name=None), prof)
    c, _, _ = api()
    card = c.get("/api/today").json()["signals"][0]
    assert card["name"] is None and card["label"]


def test_s04_actions_waiting_for_approval(conn, clock, api):
    pid = person(conn, clock)
    db.run(conn, "INSERT INTO action_queue (kind, person_id, payload) VALUES ('first_message', %s, %s)",
           (pid, db.j({"text": "Здравствуйте"})))
    db.run(conn, "INSERT INTO action_queue (kind, person_id, state) VALUES ('follow_up', %s, 'executed')", (pid,))
    c, _, _ = api()
    acts = c.get("/api/today").json()["actions"]
    assert [a["kind"] for a in acts] == ["first_message"] and acts[0]["person_id"] == pid


def test_s37_stalled(conn, clock, api):
    cid = candidate(conn, person(conn, clock), silent_days=6)
    candidate(conn, person(conn, clock, rid="fresh"), silent_days=1)
    c, _, _ = api()
    st = c.get("/api/today").json()["stalled"]
    assert [s["candidate_id"] for s in st] == [cid] and st[0]["days_silent"] >= 5


def test_s36_meetings_today_and_tomorrow(conn, clock, api):
    cid = candidate(conn, person(conn, clock), silent_days=0)
    for delta in (timedelta(hours=2), timedelta(days=1, hours=1), timedelta(days=3)):
        db.run(conn, "INSERT INTO interview (candidate_id, kind, scheduled_at) VALUES (%s, 'phone', %s)",
               (cid, clock.now() + delta))
    c, _, _ = api()
    meet = c.get("/api/today").json()["meetings"]
    assert len(meet) == 2 and meet[0]["scheduled_at"] < meet[1]["scheduled_at"]
    assert meet[0]["kind"] == "phone" and meet[0]["person_id"]


def test_signal_card_has_resume_summary(conn, clock, api):
    prof = profile(conn)
    alert(conn, person(conn, clock, rid="sum", salary={"amount": 180000, "currency": "RUR"},
                       relocation={"type": {"id": "relocation_possible"}}), prof)
    c, _, _ = api()
    card = c.get("/api/today").json()["signals"][0]
    s = card["summary"]
    assert s["exp_years"] and s["salary_expect"] == 180000 and s["relocation_ready"] is True
    assert s["employer_current"] and s["skills"][:1] == ["Altium Designer"] and len(s["skills"]) <= 5
    assert "updated_at_src" in s
