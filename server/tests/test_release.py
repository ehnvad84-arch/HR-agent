"""Фича releases: журнал версий, смена умолчаний настроек, замок миграций."""
import hashlib
import re
import shutil
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


def test_cli_release_last(conn, cli):
    """S5: rollback.sh узнаёт, с какой версии было последнее удачное обновление."""
    assert cli("release-last") == (5, "", "")
    cli("release-record", "--kind", "baseline", "--to", "v1.0.0")
    cli("release-record", "--kind", "update", "--from", "v1.0.0", "--to", "v1.1.0")
    cli("release-record", "--kind", "update", "--from", "v1.1.0", "--to", "v1.2.0")
    cli("release-record", "--kind", "update", "--from", "v1.2.0", "--to", "v1.3.0", "--failed")
    cli("release-record", "--kind", "rollback", "--from", "v1.2.0", "--to", "v1.1.0")
    assert cli("release-last")[:2] == (0, "v1.1.0 v1.2.0\n")
    assert cli("release-last", "--kind", "rollback")[:2] == (0, "v1.2.0 v1.1.0\n")
    assert cli("release-last", "--kind", "baseline")[:2] == (0, "- v1.0.0\n")


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


# --- Замок выпущенных миграций (S7), линтер DDL (S6), CHANGELOG ↔ VERSION (S10) ---

SECTIONS = ("Что нового", "Миграции", "Переменные .env", "Ручные шаги", "Откат")
# Сужение схемы и правка данных, на которых прошлая версия ломается на новой базе (S6)
NARROWING = {
    r"\bDROP\b": "DROP",
    r"\bRENAME\b": "RENAME",
    r"\bALTER\s+COLUMN\s+\S+\s+(SET\s+DATA\s+)?TYPE\b": "ALTER COLUMN … TYPE",
    r"\bSET\s+NOT\s+NULL\b": "SET NOT NULL",
    r"\bADD\s+CONSTRAINT\b": "ADD CONSTRAINT",
    r"\bCREATE\s+UNIQUE\s+INDEX\b": "CREATE UNIQUE INDEX",
    r"\bDELETE\s+FROM\b": "DELETE",
    r"\bTRUNCATE\b": "TRUNCATE",
    r"\bUPDATE\s+(public\.)?settings\b": "UPDATE settings (только ops.change_default)",
}


def released(mig_dir):
    """Имена выпущенных миграций из замка RELEASED.sha256 (`sha256  имя`)."""
    lines = (mig_dir / "RELEASED.sha256").read_text(encoding="utf-8").splitlines()
    return {ln.split(maxsplit=1)[1].strip(): ln.split()[0] for ln in lines if ln.strip()}


def lock_problems(mig_dir):
    """S7: каждая выпущенная миграция на месте и не изменена; номера NNN_ уникальны."""
    if not (mig_dir / "RELEASED.sha256").exists():
        return ["нет RELEASED.sha256"]
    out = []
    for name, digest in released(mig_dir).items():
        f = mig_dir / name
        if not f.exists():
            out.append(f"{name}: выпущенная миграция удалена")
        elif hashlib.sha256(f.read_bytes()).hexdigest() != digest:
            out.append(f"{name}: выпущенная миграция изменена — исправление только новой миграцией")
    seen = {}
    for f in sorted(mig_dir.glob("*.sql")):
        m = re.match(r"(\d{3})_", f.name)
        if not m:
            out.append(f"{f.name}: имя не по образцу NNN_<что>.sql")
        elif m[1] in seen:
            out.append(f"{f.name}: номер {m[1]} уже занят {seen[m[1]]}")
        else:
            seen[m[1]] = f.name
    return out


def _strip_sql(text):
    """Без тел $$…$$ и $tag$…$tag$, строк '…' и комментариев --: линтер смотрит только DDL."""
    text = re.sub(r"\$(\w*)\$.*?\$\1\$", " ", text, flags=re.S)
    text = re.sub(r"'(?:[^']|'')*'", "''", text)
    return re.sub(r"--[^\n]*", " ", text)


def _top_clauses(stmt):
    """Части ALTER TABLE через запятую вне скобок."""
    out, depth, cur = [], 0, ""
    for ch in stmt:
        depth += (ch == "(") - (ch == ")")
        if ch == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    return [*out, cur]


def ddl_problems(text):
    """S6: что миграция без строки `-- contract: <причина>` делать не может."""
    if re.search(r"^\s*--\s*contract:\s*\S", text, flags=re.M | re.I):
        return []
    sql = _strip_sql(text)
    out = [name for rx, name in NARROWING.items() if re.search(rx, sql, flags=re.I)]
    for stmt in sql.split(";"):
        if re.match(r"\s*ALTER\s+TABLE\b", stmt, flags=re.I):
            for cl in _top_clauses(stmt):
                add = re.search(r"\bADD\s+(?!CONSTRAINT\b)(.*)", cl, flags=re.I | re.S)
                if add and re.search(r"\bNOT\s+NULL\b", add[1], flags=re.I) \
                        and not re.search(r"\bDEFAULT\b", add[1], flags=re.I):
                    out.append("ADD COLUMN … NOT NULL без DEFAULT")
        if re.match(r"\s*INSERT\s+INTO\s+(public\.)?settings\b", stmt, flags=re.I) \
                and not re.search(r"\bON\s+CONFLICT\b.*\bDO\s+NOTHING\b", stmt, flags=re.I | re.S):
            out.append("INSERT INTO settings без ON CONFLICT DO NOTHING")
    return out


def changelog_problems(changelog, ver, new_migrations):
    """S10: первый раздел CHANGELOG — версия из VERSION, пять подразделов, новые миграции названы."""
    heads = list(re.finditer(r"^## (\S+) — (\d{4}-\d{2}-\d{2})\s*$", changelog, flags=re.M))
    if not heads:
        return ["в CHANGELOG.md нет раздела версии «## vX.Y.Z — ГГГГ-ММ-ДД»"]
    if heads[0][1] != ver:
        return [f"первый раздел CHANGELOG.md — {heads[0][1]}, а VERSION — {ver}"]
    end = heads[1].start() if len(heads) > 1 else len(changelog)
    body = changelog[heads[0].end():end]
    subs = dict(re.findall(r"^### (.+?)[ \t]*\n(.*?)(?=^### |\Z)", body, flags=re.M | re.S))
    out = [f"в разделе {ver} нет «### {s}»" for s in SECTIONS if s not in subs]
    out += [f"миграция {m} не названа в «Миграции» раздела {ver}" for m in sorted(new_migrations)
            if m not in subs.get("Миграции", "")]
    return out


def test_s7_released_migrations_locked():
    """S7: в замке 001–008 из v1.0.0, все суммы сходятся, номера уникальны."""
    assert lock_problems(db.MIGRATIONS_DIR) == []
    assert {f"{n:03d}" for n in range(1, 9)} <= {name[:3] for name in released(db.MIGRATIONS_DIR)}


def test_s7_changed_deleted_or_duplicate_migration_fails(tmp_path):
    for f in [*db.migration_files(), db.MIGRATIONS_DIR / "RELEASED.sha256"]:
        shutil.copy(f, tmp_path)
    assert lock_problems(tmp_path) == []
    with (tmp_path / "003_digest.sql").open("a", encoding="utf-8") as fh:
        fh.write("\n-- правка\n")
    (tmp_path / "005_org_progress.sql").unlink()
    (tmp_path / "009_another.sql").write_text("SELECT 1;\n", encoding="utf-8")
    (tmp_path / "bad.sql").write_text("SELECT 1;\n", encoding="utf-8")
    probs = lock_problems(tmp_path)
    assert "003_digest.sql: выпущенная миграция изменена — исправление только новой миграцией" in probs
    assert "005_org_progress.sql: выпущенная миграция удалена" in probs
    assert "009_release.sql: номер 009 уже занят 009_another.sql" in probs
    assert "bad.sql: имя не по образцу NNN_<что>.sql" in probs
    (tmp_path / "RELEASED.sha256").unlink()
    assert lock_problems(tmp_path) == ["нет RELEASED.sha256"]


def test_s6_unreleased_migrations_only_add():
    """S6: миграции вне замка (сейчас 009) проходят линтер DDL."""
    new = [f for f in db.migration_files() if f.name not in released(db.MIGRATIONS_DIR)]
    assert [f.name for f in new] == ["009_release.sql"]
    assert {f.name: ddl_problems(f.read_text(encoding="utf-8")) for f in new} == {f.name: [] for f in new}


@pytest.mark.parametrize("sql, problem", [
    ("DROP TABLE x;", "DROP"),
    ("ALTER TABLE x DROP COLUMN y;", "DROP"),
    ("ALTER TABLE x RENAME COLUMN a TO b;", "RENAME"),
    ("ALTER TABLE x ALTER COLUMN a TYPE bigint;", "ALTER COLUMN … TYPE"),
    ("ALTER TABLE x ALTER COLUMN a SET NOT NULL;", "SET NOT NULL"),
    ("ALTER TABLE x ADD COLUMN a int NOT NULL;", "ADD COLUMN … NOT NULL без DEFAULT"),
    ("ALTER TABLE x ADD COLUMN b int, ADD a text NOT NULL;", "ADD COLUMN … NOT NULL без DEFAULT"),
    ("ALTER TABLE x ADD CONSTRAINT c CHECK (a > 0);", "ADD CONSTRAINT"),
    ("CREATE UNIQUE INDEX i ON x (a);", "CREATE UNIQUE INDEX"),
    ("DELETE FROM x WHERE a = 1;", "DELETE"),
    ("TRUNCATE x;", "TRUNCATE"),
    ("UPDATE settings SET value = '1' WHERE key = 'k';", "UPDATE settings (только ops.change_default)"),
    ("INSERT INTO settings (key, value) VALUES ('k', '1');", "INSERT INTO settings без ON CONFLICT DO NOTHING"),
    ("INSERT INTO settings (key, value) VALUES ('k', '1') ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;",
     "INSERT INTO settings без ON CONFLICT DO NOTHING"),
])
def test_s6_narrowing_without_contract_fails(sql, problem):
    assert problem in ddl_problems(sql)
    assert ddl_problems(f"-- contract: код v1.1 больше не читает это поле\n{sql}") == []


@pytest.mark.parametrize("sql", [
    "CREATE TABLE IF NOT EXISTS t (id bigserial PRIMARY KEY, a text NOT NULL,"
    " b bigint REFERENCES x ON DELETE CASCADE, UNIQUE (a));",
    "ALTER TABLE x ADD COLUMN IF NOT EXISTS a int NOT NULL DEFAULT 0, ADD COLUMN b text;",
    "CREATE INDEX IF NOT EXISTS i ON x (a);",
    "ALTER TYPE k ADD VALUE IF NOT EXISTS 'new';",
    "INSERT INTO settings (key, value) VALUES ('k', '1') ON CONFLICT (key) DO NOTHING;",
    "SELECT ops.change_default('010_x.sql', 'k', '1', '2');",
    "CREATE OR REPLACE FUNCTION f() RETURNS void LANGUAGE plpgsql AS $$\n"
    "BEGIN DELETE FROM x; UPDATE settings SET value = '1'; DROP TABLE y; END;\n$$;",
    "COMMENT ON TABLE x IS 'DROP и RENAME здесь — просто слова'; -- DROP в комментарии",
])
def test_s6_additive_migration_passes(sql):
    assert ddl_problems(sql) == []


def test_s10_changelog_matches_version():
    """S10: первый раздел CHANGELOG — версия из VERSION, пять подразделов, новые миграции названы."""
    ver = (version.ROOT / "VERSION").read_text(encoding="utf-8").strip()
    new = {f.name for f in db.migration_files()} - set(released(db.MIGRATIONS_DIR))
    assert changelog_problems((version.ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), ver, new) == []


def test_s10_version_without_changelog_section_fails():
    ok = ("## v1.2.0 — 2026-12-01\n\n### Что нового\n\n- x\n\n### Миграции\n\n- `010_x.sql`\n\n"
          "### Переменные .env\n\nНовых нет.\n\n### Ручные шаги\n\nНет.\n\n### Откат\n\nда\n\n"
          "## v1.1.0 — 2026-11-10\n\n### Что нового\n")
    assert changelog_problems(ok, "v1.2.0", {"010_x.sql"}) == []
    assert changelog_problems(ok, "v1.3.0", set()) == ["первый раздел CHANGELOG.md — v1.2.0, а VERSION — v1.3.0"]
    assert changelog_problems("# Список\n", "v1.2.0", set()) == [
        "в CHANGELOG.md нет раздела версии «## vX.Y.Z — ГГГГ-ММ-ДД»"]
    assert changelog_problems(ok, "v1.2.0", {"010_x.sql", "011_y.sql"}) == [
        "миграция 011_y.sql не названа в «Миграции» раздела v1.2.0"]
    assert changelog_problems(ok.replace("### Откат\n\nда\n\n", ""), "v1.2.0", {"010_x.sql"}) == [
        "в разделе v1.2.0 нет «### Откат»"]
