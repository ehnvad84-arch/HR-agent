"""T20: задачи score и alerts, запуск командой (S16, S19, S21)."""
import json

import httpx
import pytest
from factories import hh_item, profile, source

from hr import cli, config, db, tasks
from hr.normalize import normalize
from hr.pipeline import process


def ingest(conn, clock, **kw):
    it = hh_item(**kw)
    return process(conn, source(conn, "hh"), normalize("hh", it), it, clock.now())


def test_registry_has_core_tasks():
    assert {"collect_hh", "mark_gone", "import_files", "collect_social", "score", "alerts"} <= set(tasks.TASKS)
    assert "source:hh" in tasks.TASKS["collect_hh"].locks and "source:hh" in tasks.TASKS["mark_gone"].locks


def test_score_task_explains_after_model_appears(conn, conn_factory, clock, monkeypatch):
    profile(conn)
    pid = ingest(conn, clock).person_id
    assert db.val(conn, "SELECT model FROM match WHERE person_id = %s", (pid,)) == "template"
    monkeypatch.setenv("LLM_BASE_URL", "http://llm.test/v1")
    monkeypatch.setenv("LLM_MODEL", "m")
    config.get_config.cache_clear()
    try:
        reply = {"why": "Совпадают инструменты.", "gap": "Нет серии.", "questions": ["Серия?"]}

        def handler(req):
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(reply)}}]})

        res = tasks.run("score", conn_factory, http=httpx.Client(transport=httpx.MockTransport(handler)), clock=clock)
        assert res.ok
        assert db.val(conn, "SELECT model FROM match WHERE person_id = %s", (pid,)).startswith("m/explain_score@")
    finally:
        config.get_config.cache_clear()


def test_alerts_task_handles_unprocessed_events(conn, conn_factory, clock):
    profile(conn)
    r = ingest(conn, clock)
    db.run(conn, "UPDATE alert SET state = 'taken'")
    db.run(conn, """INSERT INTO obs_event (observation_id, person_id, kind, happened_at)
                    VALUES (%s, %s, 'disappeared', %s)""", (r.observation_id, r.person_id, clock.now()))
    res = tasks.run("alerts", conn_factory, clock=clock)
    assert res.ok and res.stats.alerts == 1
    a = db.one(conn, "SELECT * FROM alert WHERE state = 'new'")
    assert a["reason"] == "event_on_pool" and "скрыто" in a["headline"]
    assert db.val(conn, "SELECT count(*) FROM obs_event WHERE NOT processed") == 0


def test_cli_run_writes_job_run(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("IMPORT_DIR", str(tmp_path))
    config.get_config.cache_clear()
    try:
        assert cli.main(["run", "import_files"]) == 0
    finally:
        config.get_config.cache_clear()
    assert "import_files: готово" in capsys.readouterr().out
    import psycopg

    with psycopg.connect(config.get_config().database_url) as c:
        assert c.execute("SELECT ok FROM ops.job_run WHERE job = 'import_files' ORDER BY id DESC LIMIT 1").fetchone()[0]


def test_cli_run_unknown_task():
    with pytest.raises(SystemExit):
        cli.main(["run", "nope"])
