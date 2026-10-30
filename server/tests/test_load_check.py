"""T17 manager-cabinet: проверка загрузок и сообщения отделу персонала (S20)."""
from datetime import UTC, datetime

from factories import user

from hr import db, jobs, load_check
from hr.jobs import JobBlocked, JobStats


def hr_users(conn):
    return user(conn, "hr1", "recruiter"), user(conn, "hr2", "head_recruiter"), user(conn, "boss", "hiring_manager")


def notes(conn, uid):
    return db.rows(conn, "SELECT kind, body FROM notification WHERE user_id = %s ORDER BY id", (uid,))


def test_ok_run_notifies_all_hr(conn, conn_factory):
    a, b, m = hr_users(conn)
    res = jobs.run("collect_hh", lambda cf, **_: JobStats(seen=40, new=7, changed=3), conn_factory)
    assert res.ok
    for uid in (a, b):
        n = notes(conn, uid)
        assert len(n) == 1 and n[0]["kind"] == "load_ok" and "просмотрено 40, новых 7" in n[0]["body"]["text"]
    assert notes(conn, m) == []


def test_failure_zero_and_errors_are_alarms(conn, conn_factory):
    a, _, _ = hr_users(conn)

    def blocked(cf, **_):
        raise JobBlocked("captcha")

    jobs.run("collect_hh", blocked, conn_factory)
    jobs.run("collect_trudvsem", lambda cf, **_: JobStats(), conn_factory)
    jobs.run("collect_hh_daily", lambda cf, **_: JobStats(seen=10, errors=5), conn_factory)
    texts = [(n["kind"], n["body"]["text"]) for n in notes(conn, a)]
    assert [k for k, _ in texts] == ["load_alarm"] * 3
    assert "проверку на робота" in texts[0][1] and "0 резюме" in texts[1][1] and "5 из 10" in texts[2][1]
    assert all("требуется разбирательство" in n["body"]["title"] for n in notes(conn, a))


def test_repeat_suppressed_and_ack(conn, conn_factory, api):
    hr_users(conn)
    for _ in range(2):
        jobs.run("collect_trudvsem", lambda cf, **_: JobStats(), conn_factory)
    c, h, uid = api("recruiter", login="hr1")
    lst = c.get("/api/notifications").json()
    assert len(lst["items"]) == 1 and lst["alarm"]["text"].startswith("пришло 0")
    assert c.post(f"/api/notification/{lst['alarm']['id']}/ack", headers=h).json()["ok"]
    other, _, _ = api("head_recruiter", login="hr2")
    got = other.get("/api/notifications").json()
    assert got["alarm"] is None and got["items"][0]["ack"]["by"] == "Проверочный hr1"
    boss, _, _ = api("hiring_manager", login="boss")
    assert boss.get("/api/notifications").status_code == 403


def test_watchdog_missed_and_stuck(conn, conn_factory):
    a, _, _ = hr_users(conn)
    db.run(conn, "INSERT INTO ops.job_run (job, started_at) VALUES ('collect_trudvsem', %s)",
           (datetime(2026, 11, 2, 22, 0, tzinfo=UTC),))   # вторник 01:00 МСК — началась и зависла

    class C:
        def now(self):
            return datetime(2026, 11, 3, 17, 0, tzinfo=UTC)   # вторник 20:00 МСК

    load_check.load_watchdog(conn_factory, clock=C())
    texts = [n["body"]["text"] for n in notes(conn, a)]
    assert any("зависла" in t for t in texts) and any("не началась по расписанию" in t for t in texts)
    assert len(texts) == 2
    load_check.load_watchdog(conn_factory, clock=C())
    assert len(notes(conn, a)) == 2   # повтор подавлен
