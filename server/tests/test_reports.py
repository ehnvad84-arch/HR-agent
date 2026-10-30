"""T19 workplace: журнал изменений и недельный отчёт (S32, S33)."""
from datetime import timedelta

from hr import db


def change(conn, kind, note, at, by=None):
    db.run(conn, """INSERT INTO agent_change (kind, before, after, applied_by, applied_at, note)
                    VALUES (%s, %s, %s, %s, %s, %s)""", (kind, db.j({"v": 1}), db.j({"v": 2}), by, at, note))


def test_s32_changelog_by_period(conn, api, clock):
    c, _, uid = api()
    change(conn, "threshold", "порог 60 → 65", clock.now() - timedelta(days=2), uid)
    change(conn, "profile", "старое", clock.now() - timedelta(days=40), uid)
    r = c.get("/api/changelog", params={"from": (clock.now() - timedelta(days=7)).date().isoformat(),
                                        "to": clock.now().date().isoformat()}).json()
    notes = [x["note"] for x in r["items"]]
    assert "порог 60 → 65" in notes and "старое" not in notes
    row = next(x for x in r["items"] if x["note"] == "порог 60 → 65")
    assert row["author"] and row["before"] == {"v": 1} and row["after"] == {"v": 2} and row["kind"] == "threshold"


def test_s33_weekly_report_build_and_read(conn, api, clock):
    c, h, uid = api()
    change(conn, "threshold", "порог", clock.now() - timedelta(days=1), uid)
    change(conn, "profile", "профиль", clock.now() - timedelta(days=2), uid)
    r = c.post("/api/report/weekly/build", json={}, headers=h)
    assert r.status_code == 200 and r.json()["changes"] == 2
    rep = c.get("/api/report/weekly").json()
    assert rep["changes"] == 2 and {g["kind"] for g in rep["body"]["groups"]} == {"threshold", "profile"}
    assert rep["sent_at"] is None


def test_report_needs_permission(api):
    c, _, _ = api("recruiter")
    assert c.get("/api/changelog").status_code == 403
    assert c.get("/api/report/weekly").status_code == 403
