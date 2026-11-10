"""Фича releases: журнал версий, смена умолчаний настроек, замок миграций."""
import subprocess

from factories import user

from hr import db, version


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
