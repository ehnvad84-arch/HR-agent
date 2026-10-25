"""T2: запуск задач и ограничения обращения к площадкам (S4)."""
import threading
from itertools import pairwise

import pytest

from hr import db, jobs
from hr.http import PageBudget, RateLimiter


def test_s04_one_request_per_second_per_source(clock):
    lim = RateLimiter(clock, per_sec=1.0)
    stamps = []
    for _ in range(4):
        lim.wait("hh")
        stamps.append(clock.monotonic())
    gaps = [b - a for a, b in pairwise(stamps)]
    assert all(g >= 1.0 for g in gaps)


def test_s04_sources_limited_independently(clock):
    lim = RateLimiter(clock, per_sec=1.0)
    lim.wait("hh")
    lim.wait("github")
    assert clock.slept == [], "разные площадки друг друга не ждут"


def test_s04_page_budget_stops_and_marks():
    stats = jobs.JobStats()
    budget = PageBudget(3, stats)
    assert [budget.take() for _ in range(4)] == [True, True, True, False]
    assert stats.pages == 3 and stats.limit_reached


def _ok(conn_factory, **kw):
    s = jobs.JobStats()
    s.seen = 2
    return s


def test_job_run_writes_journal(conn, conn_factory):
    res = jobs.run("collect_test", _ok, conn_factory)
    assert res.ok and res.stats.seen == 2
    row = db.one(conn, "SELECT * FROM ops.job_run WHERE job = 'collect_test' ORDER BY id DESC LIMIT 1")
    assert row["ok"] and row["finished_at"] and row["stats"]["seen"] == 2


def test_job_known_errors_keep_text_and_mark_blocked(conn, conn_factory):
    def blocked(conn_factory, **kw):
        raise jobs.JobBlocked("hh просит пройти проверку на робота")

    res = jobs.run("collect_test", blocked, conn_factory)
    assert not res.ok and "проверку" in res.error
    row = db.one(conn, "SELECT * FROM ops.job_run WHERE job = 'collect_test' ORDER BY id DESC LIMIT 1")
    assert row["ok"] is False and row["stats"]["blocked"] is True


def test_job_unexpected_error_logs_class_only(conn, conn_factory):
    def boom(conn_factory, **kw):
        raise ValueError("Иванов Иван +7 900 000-00-00")

    res = jobs.run("collect_test", boom, conn_factory)
    assert not res.ok and res.retry
    assert res.error == "ValueError"
    row = db.one(conn, "SELECT error FROM ops.job_run WHERE job = 'collect_test' ORDER BY id DESC LIMIT 1")
    assert "Иванов" not in row["error"]


def test_job_record_errors_are_not_failure(conn_factory):
    def partly(conn_factory, **kw):
        s = jobs.JobStats()
        s.errors = 3
        return s

    res = jobs.run("collect_test", partly, conn_factory)
    assert res.ok and res.error == "3 записей не принято"


def test_job_parallel_run_is_busy(conn_factory):
    started, release = threading.Event(), threading.Event()
    results = {}

    def slow(conn_factory, **kw):
        started.set()
        release.wait(5)
        return jobs.JobStats()

    t = threading.Thread(target=lambda: results.setdefault("first", jobs.run("busy_test", slow, None)))
    t.start()
    started.wait(5)
    second = jobs.run("busy_test", _ok, conn_factory)
    release.set()
    t.join(5)
    assert not second.ok and isinstance(second.exc, jobs.JobBusy) and not second.retry
    assert results["first"].ok


@pytest.mark.parametrize("exc", [jobs.JobBlocked, jobs.JobNotConfigured, jobs.JobBusy])
def test_no_retry_for_known_errors(exc, conn_factory):
    def f(conn_factory, **kw):
        raise exc("x")

    assert jobs.run("collect_test", f, conn_factory).retry is False
