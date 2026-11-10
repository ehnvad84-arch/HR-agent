"""Фича releases: журнал версий, смена умолчаний настроек, замок миграций."""
import subprocess

import pytest
from factories import user

from hr import cli as hr_cli
from hr import db, release, version


def _change(conn, key, old, new):
    return db.val(conn, "SELECT ops.change_default('999_test.sql', %s, %s, %s, 'проверка')",
                  (key, db.j(old), db.j(new)))


def _changes(conn, key):
    return db.rows(conn, "SELECT before, after, applied_by, note FROM agent_change WHERE after->>'setting' = %s",
                   (key,))


def _notices(conn, key):
    return [r["message"] for r in db.rows(conn, "SELECT message FROM ops.release_notice WHERE setting = %s", (key,))]


def test_release_tables(conn):
    names = {r["table_name"] for r in db.rows(conn, """SELECT table_name FROM information_schema.tables
                                                        WHERE table_schema = 'ops'""")}
    assert {"release", "release_notice"} <= names


def test_s8_untouched_setting_gets_new_default(conn):
    old = db.setting(conn, "alert_score_new")
    assert _change(conn, "alert_score_new", old, 80) is True
    assert db.setting(conn, "alert_score_new") == 80
    [ch] = _changes(conn, "alert_score_new")
    assert ch["before"] == {"setting": "alert_score_new", "value": old} and ch["after"]["value"] == 80
    assert ch["applied_by"] is None and "999_test.sql" in ch["note"]
    assert _notices(conn, "alert_score_new") == []


def test_s8_setting_changed_by_human_in_portal_is_kept(conn):
    """Правка через сводку пишет agent_change с applied_by: даже если значение вернули к умолчанию, не трогаем."""
    old = db.setting(conn, "alert_score_new")
    hr = user(conn, "head", "head_recruiter")
    db.run(conn, """INSERT INTO agent_change (kind, before, after, applied_by, note)
                    VALUES ('threshold', %s, %s, %s, 'сводка')""",
           (db.j({"setting": "alert_score_new", "value": old}), db.j({"setting": "alert_score_new", "value": old}),
            hr))
    assert _change(conn, "alert_score_new", old, 80) is False
    assert db.setting(conn, "alert_score_new") == old
    assert _notices(conn, "alert_score_new") == ["настройка alert_score_new оставлена как есть"]


def test_s8_setting_changed_by_sql_is_kept(conn):
    db.run(conn, "UPDATE settings SET value = '70' WHERE key = 'alert_score_new'")
    assert _change(conn, "alert_score_new", 75, 80) is False
    assert db.setting(conn, "alert_score_new") == 70
    assert _changes(conn, "alert_score_new") == []
    assert _notices(conn, "alert_score_new") == ["настройка alert_score_new оставлена как есть"]


def test_s8_missing_key_is_inserted(conn):
    assert db.setting(conn, "release_test_key") is None
    assert _change(conn, "release_test_key", None, {"a": 1}) is True
    assert db.setting(conn, "release_test_key") == {"a": 1}
    [ch] = _changes(conn, "release_test_key")
    assert ch["before"] is None and ch["after"] == {"setting": "release_test_key", "value": {"a": 1}}


def test_s8_repeat_with_new_value_does_nothing(conn):
    old = db.setting(conn, "alert_score_new")
    _change(conn, "alert_score_new", old, 80)
    assert _change(conn, "alert_score_new", old, 80) is False
    assert len(_changes(conn, "alert_score_new")) == 1 and _notices(conn, "alert_score_new") == []


def test_s8_repeat_migrate_changes_nothing(conn):
    assert db.migrate() == []


def test_version_status(conn, tmp_path):
    """S1: версия кода, последняя удачная запись журнала, неприменённые и «лишние» миграции."""
    code = (version.ROOT / "VERSION").read_text(encoding="utf-8").strip()
    st = version.status(conn)
    assert st == {"code": code, "db": None, "applied": len(db.migration_files()), "pending": [], "ahead": []}
    assert version.release_date(code) is not None

    db.run(conn, """INSERT INTO ops.release (kind, to_version, ok, started_at) VALUES
                    ('baseline', 'v1.0.0', true, now() - interval '2 hours'),
                    ('update', 'v1.1.0', true, now() - interval '1 hour'),
                    ('update', 'v1.2.0', false, now())""")
    db.run(conn, "DELETE FROM ops.schema_migration WHERE name = '009_release.sql'")
    db.run(conn, "INSERT INTO ops.schema_migration (name) VALUES ('010_from_newer.sql')")
    st = version.status(conn)
    assert st["db"] == "v1.1.0", "неудачное обновление не считается"
    assert st["pending"] == ["009_release.sql"] and db.pending(conn) == ["009_release.sql"]
    assert st["ahead"] == ["010_from_newer.sql"], "после отката база впереди кода — это норма (S5)"


@pytest.fixture
def cli(conn_factory, monkeypatch, capsys):
    """hr.cli на транзакции теста: (код выхода, stdout, stderr)."""
    monkeypatch.setattr(db, "tx", conn_factory)

    def run(*argv):
        code = hr_cli.main(list(argv))
        out = capsys.readouterr()
        return code, out.out, out.err

    return run


def test_cli_version_and_pending_exit_codes(conn, cli):
    """S1: версия, число применённых; выход 3 — есть неприменённые."""
    code, out, _ = cli("version")
    assert code == 0 and version.code_version() in out and "неприменённых нет" in out
    assert f"миграций применено: {len(db.migration_files())}" in out
    assert cli("migrate", "--pending")[:2] == (0, "неприменённых нет\n")

    db.run(conn, "DELETE FROM ops.schema_migration WHERE name = '009_release.sql'")
    code, out, _ = cli("version")
    assert code == 3 and "не применены: 009_release.sql" in out
    assert cli("migrate", "--pending")[:2] == (3, "не применены: 009_release.sql\n")
    assert db.applied(conn) == {f.name for f in db.migration_files()} - {"009_release.sql"}, "--pending не применяет"


def test_cli_jobs_running(conn, cli):
    """S3: идущая задача — выход 4; зависшая (старше load_stuck_hours) — только предупреждение."""
    db.run(conn, "DELETE FROM ops.job_run")
    assert cli("jobs-running")[0] == 0
    db.run(conn, "INSERT INTO ops.job_run (job, started_at) VALUES ('collect_hh', now() - interval '5 hours')")
    db.run(conn, "INSERT INTO ops.job_run (job, started_at, finished_at, ok) VALUES ('score', now(), now(), true)")
    code, out, err = cli("jobs-running")
    assert code == 0 and "collect_hh" in err and "зависла" in err and "не идёт" in out

    db.run(conn, "INSERT INTO ops.job_run (job, started_at) VALUES ('collect_trudvsem', now() - interval '1 hour')")
    code, out, _ = cli("jobs-running")
    assert code == 4 and "идёт: collect_trudvsem" in out

    db.run(conn, "UPDATE settings SET value = '0.5' WHERE key = 'load_stuck_hours'")
    code, _, err = cli("jobs-running")
    assert code == 0 and "collect_trudvsem" in err, "порог — из настройки"


def test_cli_release_record(conn, cli):
    code, out, _ = cli("release-record", "--kind", "update", "--from", "v1.0.0", "--to", "v1.1.0", "--commit", "abc",
                       "--migrations", "009_release.sql", "--actor", "инженер", "--note", "проба")
    assert code == 0 and "журнал версий: запись" in out
    r = db.one(conn, "SELECT * FROM ops.release ORDER BY id DESC LIMIT 1")
    assert (r["kind"], r["from_version"], r["to_version"], r["git_commit"], r["migrations"], r["actor"], r["ok"]) == \
        ("update", "v1.0.0", "v1.1.0", "abc", ["009_release.sql"], "инженер", True)
    assert r["finished_at"] is not None and version.status(conn)["db"] == "v1.1.0"

    cli("release-record", "--kind", "auto_rollback", "--from", "v1.1.0", "--to", "v1.2.0", "--failed")
    r = db.one(conn, "SELECT * FROM ops.release ORDER BY id DESC LIMIT 1")
    assert r["kind"] == "auto_rollback" and r["ok"] is False and r["actor"]
    assert version.status(conn)["db"] == "v1.1.0"
    with pytest.raises(SystemExit):
        cli("release-record", "--kind", "deploy", "--to", "v1.1.0")


def test_release_notices_of_run(conn):
    """S8: migrate печатает заметки только своих миграций."""
    db.run(conn, "UPDATE settings SET value = '70' WHERE key = 'alert_score_new'")
    _change(conn, "alert_score_new", 75, 80)
    assert release.notices(conn, ["999_test.sql"]) == ["настройка alert_score_new оставлена как есть"]
    assert release.notices(conn, ["009_release.sql"]) == [] and release.notices(conn, []) == []


def test_cli_env_missing_prints_names_only(tmp_path, cli):
    """S9: только имена переменных, которых нет или пусто; значения не печатаются."""
    example = tmp_path / ".env.example"
    example.write_text("# комментарий\nDATABASE_URL=postgresql://x\nSECRETS_KEY=\n# OPTIONAL_KEY=\n"
                       "BOT_TOKEN=\nLLM_MODEL=\nexport HH_CLIENT_ID=\n", encoding="utf-8")
    env = tmp_path / ".env"
    env.write_text("DATABASE_URL=postgresql://secret-host/db\nSECRETS_KEY='s3cr3t-value'\nLLM_MODEL=\"\"\n"
                   "HH_CLIENT_ID = id-value\n", encoding="utf-8")
    code, out, err = cli("env-missing", "--example", str(example), "--env", str(env))
    assert code == 0 and out == "не заданы в .env: BOT_TOKEN, LLM_MODEL\n" and err == ""
    for secret in ("secret-host", "s3cr3t", "id-value"):
        assert secret not in out
    env.write_text("DATABASE_URL=a\nSECRETS_KEY=b\nBOT_TOKEN=c\nLLM_MODEL=d\nHH_CLIENT_ID=e\n", encoding="utf-8")
    assert cli("env-missing", "--example", str(example), "--env", str(env))[1] == \
        "все переменные из .env.example заданы\n"
    assert release.env_missing(example, tmp_path / "нет") == \
        ["DATABASE_URL", "SECRETS_KEY", "BOT_TOKEN", "LLM_MODEL", "HH_CLIENT_ID"]


def test_cli_job_record(conn, cli):
    """S12: итог копии базы — в истории задач."""
    db.run(conn, "DELETE FROM ops.job_run")
    assert cli("job-record", "db_copy", "--stats", '{"bytes": 1024}')[0] == 0
    assert cli("job-record", "db_copy", "--failed", "--error", "мало места")[0] == 0
    ok, bad = db.rows(conn, "SELECT ok, stats, error, finished_at FROM ops.job_run WHERE job = 'db_copy' ORDER BY id")
    assert ok["ok"] is True and ok["stats"] == {"bytes": 1024} and ok["finished_at"] is not None
    assert bad["ok"] is False and bad["error"] == "мало места"
    assert cli("jobs-running")[0] == 0


def test_version_code_without_version_file(tmp_path):
    """Установка до v1.1.0: VERSION нет — тег текущего коммита, иначе unknown."""
    assert version.code_version(tmp_path) == "unknown"
    git = ["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "v1.0.0"], check=True)
    assert version.code_version(tmp_path) == "unknown"
    subprocess.run([*git, "tag", "v1.0.0"], check=True)
    assert version.code_version(tmp_path) == "v1.0.0"
    (tmp_path / "VERSION").write_text("v1.1.0\n", encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text("# x\n\n## v1.1.0 — 2026-11-10\n", encoding="utf-8")
    assert version.code_version(tmp_path) == "v1.1.0"
    assert version.release_date(root=tmp_path) == "2026-11-10"
    assert version.release_date("v9.9.9", tmp_path) is None


def test_api_version_without_login(monkeypatch):
    """S1: /api/version без cookie — ровно номер и дата версии, ничего больше."""
    from fastapi.testclient import TestClient

    from hr import config
    from hr.main import app
    monkeypatch.setenv("SESSION_KEY", "k" * 40)
    config.get_config.cache_clear()
    try:
        r = TestClient(app, base_url="https://localhost:8000").get("/api/version")
    finally:
        config.get_config.cache_clear()
    assert r.status_code == 200
    assert r.json() == {"version": version.code_version(), "date": version.release_date()}
    assert r.json()["version"] == "v1.1.0" and r.json()["date"] == "2026-11-10"
