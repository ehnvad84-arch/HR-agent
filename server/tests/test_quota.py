"""S11, S12: суточные пределы (ops.daily_quota) и правка настройки человеком из hr.cli."""
from __future__ import annotations

import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime

import psycopg
import pytest
from conftest import DSN
from factories import user
from psycopg.rows import dict_row

from hr import cli as hr_cli
from hr import db, quota

# 20:59 UTC — 23:59 по Москве; 21:00 UTC — уже следующие сутки
LATE = datetime(2026, 11, 22, 20, 59, tzinfo=UTC)
NEXT_DAY = datetime(2026, 11, 22, 21, 0, tzinfo=UTC)


def _used(conn, key):
    return db.rows(conn, "SELECT day, used FROM ops.daily_quota WHERE key = %s ORDER BY day", (key,))


def test_s11_take_counts_up_to_cap(conn_factory, conn):
    assert [quota.take(conn_factory, "hh_full_resume", 2, now=LATE) for _ in range(3)] == [True, True, False]
    [r] = _used(conn, "hh_full_resume")
    assert r["used"] == 2 and str(r["day"]) == "2026-11-22"


@pytest.mark.parametrize("cap", [0, -5])
def test_s12_zero_or_negative_cap_refuses(conn_factory, conn, cap):
    assert quota.take(conn_factory, "llm_call", cap, now=LATE) is False
    assert _used(conn, "llm_call") == []


def test_s11_new_moscow_day_starts_from_zero(conn_factory, conn):
    assert quota.take(conn_factory, "hh_full_resume", 1, now=LATE) is True
    assert quota.take(conn_factory, "hh_full_resume", 1, now=LATE) is False
    assert quota.take(conn_factory, "hh_full_resume", 1, now=NEXT_DAY) is True
    assert [(str(r["day"]), r["used"]) for r in _used(conn, "hh_full_resume")] == [
        ("2026-11-22", 1), ("2026-11-23", 1)]


def test_s11_parallel_take_never_exceeds_cap():
    """Настоящие параллельные транзакции: десять прогонов делят предел 3 — ровно три удачи."""
    key = f"test_{uuid.uuid4().hex}"

    @contextmanager
    def factory():
        with psycopg.connect(DSN, row_factory=dict_row) as c, c.transaction():
            yield c

    gate = threading.Barrier(10)
    results: list[bool] = []

    def worker():
        gate.wait()
        results.append(quota.take(factory, key, 3, now=LATE))

    threads = [threading.Thread(target=worker) for _ in range(10)]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sorted(results) == [False] * 7 + [True] * 3
        with psycopg.connect(DSN) as c:
            assert c.execute("SELECT used FROM ops.daily_quota WHERE key = %s", (key,)).fetchone()[0] == 3
    finally:
        with psycopg.connect(DSN, autocommit=True) as c:
            c.execute("DELETE FROM ops.daily_quota WHERE key = %s", (key,))


@pytest.fixture
def cli(conn_factory, monkeypatch, capsys):
    monkeypatch.setattr(db, "tx", conn_factory)

    def run(*argv):
        code = hr_cli.main(list(argv))
        out = capsys.readouterr()
        return code, out.out, out.err

    return run


def test_s12_cli_setting_set_writes_agent_change(conn, cli):
    uid = user(conn, "admin", "head_recruiter")
    code, out, _ = cli("setting", "set", "llm_assist_enabled", "false", "--by", "admin")
    assert code == 0 and "llm_assist_enabled" in out
    assert db.setting(conn, "llm_assist_enabled") is False
    ch = db.one(conn, """SELECT kind, before, after, applied_by FROM agent_change
                         WHERE after->>'setting' = 'llm_assist_enabled' AND applied_by IS NOT NULL""")
    assert ch == {"kind": "threshold", "before": {"setting": "llm_assist_enabled", "value": True},
                  "after": {"setting": "llm_assist_enabled", "value": False}, "applied_by": uid}


def test_s12_cli_setting_kept_by_change_default(conn, cli):
    """Правка человека из CLI не перетирается следующим выпуском, даже если вернули прежнее значение."""
    user(conn, "admin", "head_recruiter")
    assert cli("setting", "set", "hh_full_resume_per_day", "300", "--by", "admin")[0] == 0
    assert db.val(conn, "SELECT ops.change_default('999_test.sql', 'hh_full_resume_per_day', '300', '250')") is False
    assert db.setting(conn, "hh_full_resume_per_day") == 300


def test_cli_setting_text_value_without_quotes(conn, cli):
    user(conn, "admin", "head_recruiter")
    assert cli("setting", "set", "embed_night_until", "05:45", "--by", "admin")[0] == 0
    assert db.setting(conn, "embed_night_until") == "05:45"


@pytest.mark.parametrize("argv, problem", [
    (("setting", "set", "no_such_key", "1", "--by", "admin"), "нет настройки no_such_key"),
    (("setting", "set", "llm_assist_enabled", "false", "--by", "nobody"), "нет пользователя nobody"),
])
def test_cli_setting_set_refuses_unknown(conn, cli, argv, problem):
    user(conn, "admin", "head_recruiter")
    before = db.setting(conn, "llm_assist_enabled")
    code, out, err = cli(*argv)
    assert code == 1 and problem in out + err
    assert db.setting(conn, "llm_assist_enabled") == before
