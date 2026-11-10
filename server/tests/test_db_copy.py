"""S12: одна свежая копия базы — scripts/db_copy.sh на подставных pg_dump, pg_restore и hr.cli, без служб и базы.

Полная проверка с настоящим pg_dump — репетиция T10."""
import fcntl
import os
import stat
import subprocess
from pathlib import Path

import pytest

from hr import cli as hr_cli

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "db_copy.sh"


def _exe(path: Path, body: str) -> Path:
    path.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
    path.chmod(0o755)
    return path


@pytest.fixture
def box(tmp_path):
    """Каталог копии с прежней копией, update.conf и подставные команды."""
    backup = tmp_path / "backup"
    backup.mkdir()
    (backup / "ruware.dump").write_bytes(b"PGDMP old")
    calls = tmp_path / "calls.log"
    # python -m hr.cli <команда> — размер базы из DB_SIZE, остальное пишется в calls.log
    py = _exe(tmp_path / "py", f"""shift 2
case "$1" in
  db-size) [ "${{DB_SIZE:-}}" = fail ] && exit 1; echo "${{DB_SIZE:-1000}}" ;;
  *) echo "$*" >> {calls} ;;
esac
""")
    restore = _exe(tmp_path / "pg_restore", '[ "$(head -c 5)" = PGDMP ]\n')
    dump_ran = tmp_path / "dump_ran"
    pg_dump = _exe(tmp_path / "pg_dump", f'touch {dump_ran}; [ "$1" = -Fc ] && printf "PGDMP new"\n')

    def run(*args, dump=str(pg_dump), factor="1.2", size="1000", **env):
        conf = tmp_path / "update.conf"
        conf.write_text(f'BACKUP_DIR="{backup}"\nSPACE_FACTOR={factor}\nPG_DUMP="{dump}"\nPG_RESTORE="{restore}"\n',
                        encoding="utf-8")
        full = {"PATH": os.environ["PATH"], "RUWARE_ROOT": str(tmp_path), "RUWARE_CONF": str(conf),
                "RUWARE_ENV": str(tmp_path / "нет.env"), "RUWARE_CODE": str(tmp_path), "RUWARE_PY": str(py),
                "DB_SIZE": size, **env}
        p = subprocess.run(["bash", str(SCRIPT), *args], env=full, capture_output=True, text=True, timeout=30)
        return p.returncode, p.stdout + p.stderr

    run.backup, run.calls, run.dump_ran = backup, calls, dump_ran
    return run


def _old_intact(box):
    assert (box.backup / "ruware.dump").read_bytes() == b"PGDMP old"
    assert not (box.backup / "ruware.dump.new").exists()


def test_s12_copy_replaces_previous(box):
    code, out = box()
    assert code == 0, out
    dump = box.backup / "ruware.dump"
    assert dump.read_bytes() == b"PGDMP new"
    assert stat.S_IMODE(dump.stat().st_mode) == 0o600
    assert not (box.backup / "ruware.dump.new").exists()
    info = (box.backup / "ruware.dump.info").read_text(encoding="utf-8")
    assert "версия: unknown" in info and "размер: 9" in info
    assert box.calls.read_text(encoding="utf-8").startswith('job-record db_copy --stats {"bytes": 9')
    assert sorted(p.name for p in box.backup.iterdir()) == [".lock", "ruware.dump", "ruware.dump.info"]


@pytest.mark.parametrize("dump, why", [("false", "pg_dump завершился с ошибкой"),
                                       ("true", "pg_dump записал пустой файл"),
                                       ("printf garbage", "pg_restore --list не читает копию")])
def test_s12_failed_dump_keeps_previous(box, dump, why):
    code, out = box(dump=dump)
    assert code == 1 and why in out and "Прежняя копия цела" in out
    _old_intact(box)
    assert box.calls.read_text(encoding="utf-8").startswith(f"job-record db_copy --failed --error {why}")


def test_s12_not_enough_space_keeps_previous(box):
    code, out = box(size=str(10**18))
    assert code == 1 and "мало места" in out
    _old_intact(box)
    assert not box.dump_ran.exists()
    assert "--failed" in box.calls.read_text(encoding="utf-8")


def test_s12_check_space_only(box):
    code, out = box("--check-space")
    assert code == 0 and "места под копию хватает" in out
    code, out = box("--check-space", size=str(10**18))
    assert code == 1 and "мало места" in out
    assert not box.dump_ran.exists() and not box.calls.exists()
    _old_intact(box)


def test_s12_db_size_unknown_fails(box):
    code, out = box(size="fail")
    assert code == 1 and "размер базы" in out
    _old_intact(box)


def test_s12_lock_busy_unless_held_by_parent(box):
    with open(box.backup / ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        code, out = box()
        assert code == 1 and "идёт обновление или другая копия" in out
        _old_intact(box)
        code, out = box(RUWARE_LOCK_HELD="1")
        assert code == 0, out
    assert (box.backup / "ruware.dump").read_bytes() == b"PGDMP new"


def test_s12_bad_start_exit_2(box, tmp_path):
    assert box("--force")[0] == 2
    assert box("--check-space", "x")[0] == 2
    assert box(factor="полтора")[0] == 2
    code, out = box(RUWARE_CONF=str(tmp_path / "нет.conf"))
    assert code == 2 and "update.conf.example" in out
    _old_intact(box)
    assert not box.dump_ran.exists()


def test_cli_db_size(conn, capsys):
    assert hr_cli.main(["db-size"]) in (0, None)
    assert int(capsys.readouterr().out) > 0
