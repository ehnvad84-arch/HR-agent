"""Фича releases: журнал версий, смена умолчаний настроек, замок миграций."""
from factories import user

from hr import db


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
