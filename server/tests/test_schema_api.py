"""T21 workplace: «Схема» — последний прогон каждой задачи и объём базы (S34)."""
from datetime import timedelta

from hr import db


def test_s34_last_run_per_job(conn, api, clock):
    t = clock.now()
    db.run(conn, """INSERT INTO ops.job_run (job, started_at, finished_at, ok, stats, error) VALUES
                    ('collect_hh', %s, %s, true, '{"seen": 100, "new": 3}', NULL),
                    ('collect_hh', %s, %s, false, '{}', 'hh просит проверку'),
                    ('score', %s, %s, true, '{"changed": 5}', NULL)""",
           (t - timedelta(days=1), t - timedelta(days=1), t, t, t, t))
    c, _, _ = api()
    r = c.get("/api/schema").json()
    jobs = {j["job"]: j for j in r["jobs"]}
    assert jobs["collect_hh"]["ok"] is False and jobs["collect_hh"]["error"] == "hh просит проверку"
    assert jobs["score"]["stats"] == {"changed": 5}
    assert {"observations", "persons", "candidates", "profiles"} <= set(r["counts"])
