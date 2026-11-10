"""S2, S3, S4, S9, S12: scripts/update.sh на временном репозитории с тегами v1.0.0, v1.1.0, v1.1.1.

Службы, база и venv подставные: python отвечает за hr.cli, START_CMD пишет файл, который читает CHECK_URL (file://).
Полная проверка с настоящим порталом, pg_dump и venv — репетиция T10."""
import fcntl
import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_NOSYSTEM": "1"}
S111 = """## v1.1.1 — 2026-11-12

### Что нового

- исправление

### Ручные шаги

- шаг выпуска 1.1.1

"""
S110 = """## v1.1.0 — 2026-11-10

### Что нового

- версии

### Ручные шаги

- шаг выпуска 1.1.0

### Откат

нет

## v1.0.0 — 2026-10-30

### Ручные шаги

- установка
"""
FAKE_PY = """#!/usr/bin/env bash
shift 2
echo "$(basename "$(dirname "$PWD")") $*" >> {log}
case "$1" in
  jobs-running) exit "${{JOBS_RC:-0}}" ;;
  db-size) echo "${{DB_SIZE:-1000}}" ;;
  migrate) [ -n "${{MIGRATE_FAIL:-}}" ] && {{ echo "миграция упала" >&2; exit 1; }}
           echo "применено: 009_release.sql"; echo "настройка alert_score_new оставлена как есть" ;;
  version) [ -f {log}.rec ] && echo "по журналу версий: v1.1.0" || echo "по журналу версий: записей нет" ;;
  env-missing) echo "не заданы в .env: BOT_TOKEN" ;;
  release-record) touch {log}.rec ;;
esac
"""


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env={**os.environ, **GIT_ENV})


def _write(path: Path, text: str, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)
    return path


def _release(src, tag):
    _git(src, "add", "-A")
    _git(src, "commit", "-qm", tag)
    _git(src, "tag", tag)


@pytest.fixture
def inst(tmp_path):
    src = tmp_path / "src"
    _git(tmp_path, "init", "-q", "-b", "main", str(src))
    _write(src / ".gitignore", ".venv/\ndata/\n.env\n")
    _write(src / "server/requirements.txt", "fastapi==1\n")
    _write(src / "server/migrations/001_core.sql", "SELECT 1;\n")
    _release(src, "v1.0.0")
    (src / "server/scripts").mkdir()
    for name in ("update.sh", "db_copy.sh"):
        shutil.copy(SCRIPTS / name, src / "server/scripts" / name)
    _write(src / ".gitignore", ".venv\n.venvs/\n.releases/\nupdate.conf\ndata/\n.env\n")
    _write(src / "server/migrations/009_release.sql", "SELECT 9;\n")
    _write(src / "VERSION", "v1.1.0\n")
    _write(src / "CHANGELOG.md", "# Список изменений\n\n" + S110)
    _release(src, "v1.1.0")
    _write(src / "VERSION", "v1.1.1\n")
    _write(src / "CHANGELOG.md", "# Список изменений\n\n" + S111 + S110)
    _release(src, "v1.1.1")
    _git(tmp_path, "clone", "-q", "--bare", str(src), str(tmp_path / "origin.git"))

    root = tmp_path / "inst"
    _git(tmp_path, "clone", "-q", str(tmp_path / "origin.git"), str(root))
    _git(root, "checkout", "-q", "--detach", "v1.0.0")
    log = tmp_path / "calls.log"
    req = hashlib.sha256(b"fastapi==1\n").hexdigest()

    def venv(tag):
        """Готовый venv версии: update.sh его не ставит заново (PyPI в тестах не нужен)."""
        _write(root / "server/.venvs" / tag / "bin/python", FAKE_PY.format(log=log), 0o755)
        _write(root / "server/.venvs" / tag / ".ruware-requirements", req + "\n")

    for tag in ("v1.0.0", "v1.1.0", "v1.1.1"):
        venv(tag)
    (root / "server/.venv").symlink_to(".venvs/v1.0.0")
    www = tmp_path / "www/api"
    www.mkdir(parents=True)
    events = tmp_path / "events.log"
    _write(tmp_path / "start.sh", f"""echo start >> {events}
v="$(git -C {root} describe --tags --exact-match HEAD)"
[ "$v" = "${{BROKEN:-}}" ] && v=v0.0.0
printf '{{"version": "%s", "date": null}}' "$v" > {www}/version
""")
    backup = tmp_path / "backup"
    dump = _write(tmp_path / "pg_dump", '[ "$1" = -Fc ] && printf "PGDMP new"\n', 0o755)
    restore = _write(tmp_path / "pg_restore", '[ "$(head -c 5)" = PGDMP ]\n', 0o755)
    _write(root / "server/update.conf", f"""STOP_CMD="echo stop >> {events}"
START_CMD="bash {tmp_path / 'start.sh'}"
CHECK_URL="file://{www}/version"
START_WAIT_S=0
JOB_WAIT_MIN=0
JOB_POLL_S=0
BACKUP_DIR="{backup}"
PG_DUMP="${{PG_DUMP_CMD:-{dump}}}"
PG_RESTORE="{restore}"
""", 0o600)

    def run(tag, *, script=SCRIPTS / "update.sh", **env):
        full = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "TMPDIR": str(tmp_path),
                "RUWARE_ENV": str(tmp_path / "нет.env"), **GIT_ENV, **env}
        p = subprocess.run(["bash", str(script), tag], cwd=root, env=full, capture_output=True, text=True,
                           timeout=60)
        return p.returncode, p.stdout + p.stderr

    def head():
        return subprocess.run(["git", "describe", "--tags", "--exact-match", "HEAD"], cwd=root,
                              capture_output=True, text=True).stdout.strip()

    run.root, run.log, run.events, run.backup, run.head, run.venv = root, log, events, backup, head, venv
    return run


def _calls(inst):
    return inst.log.read_text(encoding="utf-8") if inst.log.exists() else ""


def _events(inst):
    return inst.events.read_text(encoding="utf-8").split() if inst.events.exists() else []


def test_s2_first_update_from_v100(inst):
    code, out = inst("v1.1.0")
    assert code == 0, out
    assert inst.head() == "v1.1.0"
    assert os.readlink(inst.root / "server/.venv") == ".venvs/v1.1.0"
    assert _events(inst) == ["stop", "start"]
    calls = _calls(inst)
    # до остановки — кодом цели из .releases/v1.1.0, после — кодом установки
    assert "v1.1.0 jobs-running" in calls and "v1.1.0 db-size" in calls
    assert "inst migrate" in calls
    assert "release-record --kind baseline --to v1.0.0" in calls
    assert "release-record --kind update --from v1.0.0 --to v1.1.0" in calls
    assert "--migrations 009_release.sql" in calls
    assert "job-record db_copy --stats" in calls
    assert (inst.backup / "ruware.dump").read_bytes() == b"PGDMP new"
    assert "Обновление прошло: v1.0.0 → v1.1.0" in out
    assert "настройка alert_score_new оставлена как есть" in out
    assert "не заданы в .env: BOT_TOKEN" in out
    assert "шаг выпуска 1.1.0" in out and "шаг выпуска 1.1.1" not in out
    assert f"копия базы: {inst.backup}/ruware.dump" in out
    assert not (inst.root / ".releases/v1.1.0").exists()
    assert sorted(p.name for p in (inst.root / "server/.venvs").iterdir()) == ["v1.0.0", "v1.1.0"]
    assert subprocess.run(["git", "status", "--porcelain"], cwd=inst.root, capture_output=True,
                          text=True).stdout == ""


def test_s2_several_versions_at_once(inst):
    code, out = inst("v1.1.1")
    assert code == 0, out
    assert inst.head() == "v1.1.1"
    assert "шаг выпуска 1.1.0" in out and "шаг выпуска 1.1.1" in out
    assert sorted(p.name for p in (inst.root / "server/.venvs").iterdir()) == ["v1.0.0", "v1.1.1"]


def test_s2_no_new_migrations_no_copy(inst):
    assert inst("v1.1.0")[0] == 0
    (inst.backup / "ruware.dump").unlink()
    inst.log.unlink()
    inst.venv("v1.1.1")
    code, out = inst("v1.1.1", script=inst.root / "server/scripts/update.sh")
    assert code == 0, out
    assert not (inst.backup / "ruware.dump").exists()
    assert "копия базы: не делалась" in out
    assert "baseline" not in _calls(inst)
    assert sorted(p.name for p in (inst.root / "server/.venvs").iterdir()) == ["v1.1.0", "v1.1.1"]


def test_s2_same_version_is_ok(inst):
    code, out = inst("v1.0.0")
    assert code == 0 and "уже установлена" in out
    assert _events(inst) == []


@pytest.mark.parametrize("tag", ["1.1.0", "v1.1", "v1.1.0;id", "--force"])
def test_bad_tag_exit_2(inst, tag):
    code, out = inst(tag)
    assert code == 2 and "использование" in out


def _refused(inst, code, out, why):
    assert code == 1, out
    assert "портал работает" in out and why in out
    assert _events(inst) == []
    assert inst.head() == "v1.0.0"


def test_s3_manual_edits_listed(inst):
    (inst.root / "server/migrations/001_core.sql").write_text("SELECT 2;\n", encoding="utf-8")
    (inst.root / "server/hotfix.py").write_text("x = 1\n", encoding="utf-8")
    code, out = inst("v1.1.0")
    _refused(inst, code, out, "ручные правки")
    assert "server/migrations/001_core.sql" in out and "server/hotfix.py" in out


def test_s3_version_not_found(inst):
    code, out = inst("v9.9.9")
    _refused(inst, code, out, "v9.9.9 не найдена")


def test_s3_older_version(inst):
    assert inst("v1.1.1")[0] == 0
    inst.events.unlink()
    code, out = inst("v1.1.0")
    assert code == 1 and "старше установленной v1.1.1" in out and "rollback.sh" in out
    assert _events(inst) == [] and inst.head() == "v1.1.1"


def test_s3_venv_not_symlink(inst):
    link = inst.root / "server/.venv"
    link.unlink()
    link.mkdir()
    code, out = inst("v1.1.0")
    _refused(inst, code, out, "«Первый раз»")


def test_s3_job_running(inst):
    code, out = inst("v1.1.0", JOBS_RC="4")
    _refused(inst, code, out, "фоновая задача идёт")


def test_s3_not_enough_space(inst):
    code, out = inst("v1.1.0", DB_SIZE=str(10**18))
    _refused(inst, code, out, "мало места")
    assert not (inst.backup / "ruware.dump").exists()


def test_s3_lock_busy(inst):
    inst.backup.mkdir()
    with open(inst.backup / ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        code, out = inst("v1.1.0")
    _refused(inst, code, out, "уже идёт обновление")


def test_s3_update_conf_writable_by_others(inst):
    (inst.root / "server/update.conf").chmod(0o666)
    code, out = inst("v1.1.0")
    assert code == 2 and "chmod 600" in out
    assert _events(inst) == []


def test_s12_copy_failed_portal_started_again(inst):
    (inst.backup).mkdir()
    (inst.backup / "ruware.dump").write_bytes(b"PGDMP old")
    code, out = inst("v1.1.0", PG_DUMP_CMD="false")
    assert code == 1 and "копия базы не записалась" in out
    assert _events(inst) == ["stop", "start"]
    assert inst.head() == "v1.0.0"
    assert (inst.backup / "ruware.dump").read_bytes() == b"PGDMP old"
    assert "migrate" not in _calls(inst)


@pytest.mark.parametrize("env, why", [({"BROKEN": "v1.1.1"}, "не ответил версией v1.1.1"),
                                      ({"MIGRATE_FAIL": "1"}, "миграция не прошла")])
def test_s4_auto_rollback(inst, env, why):
    assert inst("v1.1.0")[0] == 0
    inst.events.unlink()
    inst.venv("v1.1.1")
    code, out = inst("v1.1.1", script=inst.root / "server/scripts/update.sh", **env)
    assert code == 1, out
    assert why in out
    assert "Обновление не прошло, работает v1.1.0, база обновлена и совместима, копия — не делалась" in out
    assert inst.head() == "v1.1.0"
    assert os.readlink(inst.root / "server/.venv") == ".venvs/v1.1.0"
    assert _events(inst)[-1] == "start"
    assert "release-record --kind auto_rollback --from v1.1.1 --to v1.1.0" in _calls(inst)
    assert not (inst.root / ".releases/v1.1.1").exists()
