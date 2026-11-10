"""S5: scripts/rollback.sh на временном репозитории (фикстура inst из test_update.py).

Установка обновляется update.sh до нужной версии, затем hr.cli в venv подменяется ответами для отката.
Полная проверка с настоящим порталом и venv — репетиция T10."""
import os
import subprocess

import pytest
from test_update import S110, S111, SCRIPTS, _calls, _events, _git, _release, _write, inst  # noqa: F401

# ruff: noqa: F811 — inst из test_update — фикстура, тесты берут её параметром

FAKE_RB = """#!/usr/bin/env bash
shift 2
echo "$(basename "$(dirname "$PWD")") $*" >> {log}
case "$1" in
  jobs-running) exit "${{JOBS_RC:-0}}" ;;
  release-last) [ "${{LAST:-}}" = none ] && exit 5; echo "${{LAST:-v1.1.0 v1.1.1}}" ;;
  release-record) [ -n "${{RECORD_FAIL:-}}" ] && exit 1; echo "журнал версий: запись 1" ;;
  version) echo "база новее кода (так бывает после отката): 010_extra.sql" ;;
esac
"""
S112 = """## v1.1.2 — 2026-11-14

### Миграции

- `010_extra.sql`

### Откат

нет — 010 меняет смысл поля

"""


def _installed(inst, *tags):
    """Установка v1.1.0 и обновления update.sh по очереди до последней из tags; venv отвечают за откат."""
    assert inst("v1.1.0")[0] == 0
    for tag in tags:
        if tag == "v1.1.0":
            continue
        inst.venv(tag)
        code, out = inst(tag, script=inst.root / "server/scripts/update.sh")
        assert code == 0, out
    for py in (inst.root / "server/.venvs").glob("*/bin/python"):
        _write(py, FAKE_RB.format(log=inst.log), 0o755)
    inst.log.unlink()
    inst.events.unlink()


def _rollback(inst, tag, **env):
    return inst(tag, script=SCRIPTS / "rollback.sh", **env)


@pytest.fixture
def v112(inst):
    """Тег v1.1.2 с «Откат: нет» в origin."""
    src, origin = inst.root.parent / "src", inst.root.parent / "origin.git"
    _write(src / "VERSION", "v1.1.2\n")
    _write(src / "server/migrations/010_extra.sql", "SELECT 10;\n")
    _write(src / "CHANGELOG.md", "# Список изменений\n\n" + S112 + S111 + S110)
    _release(src, "v1.1.2")
    _git(src, "push", "-q", str(origin), "v1.1.2")
    return inst


def test_s5_rollback_code_only(inst):
    _installed(inst, "v1.1.1")
    code, out = _rollback(inst, "v1.1.0")
    assert code == 0, out
    assert inst.head() == "v1.1.0"
    assert os.readlink(inst.root / "server/.venv") == ".venvs/v1.1.0"
    assert _events(inst) == ["stop", "start"]
    calls = _calls(inst)
    assert "migrate" not in calls, "база не меняется"
    assert "inst release-record --kind rollback --from v1.1.1 --to v1.1.0" in calls
    assert calls.index("release-record") < calls.index("version"), "журнал — кодом текущей версии до checkout"
    assert "Откат прошёл: v1.1.1 → v1.1.0" in out and "база не менялась" in out
    assert "миграции новее кода остаются в базе: 010_extra.sql" in out
    assert sorted(p.name for p in (inst.root / "server/.venvs").iterdir()) == ["v1.1.0", "v1.1.1"]
    assert subprocess.run(["git", "status", "--porcelain"], cwd=inst.root, capture_output=True,
                          text=True).stdout == ""


def test_s5_same_version_is_ok(inst):
    _installed(inst, "v1.1.0")
    code, out = _rollback(inst, "v1.1.0")
    assert code == 0 and "уже установлена" in out and _events(inst) == []


def _refused(inst, code, out, why, head):
    assert code == 1, out
    assert "Откат не начат, портал работает" in out and why in out
    assert _events(inst) == [] and inst.head() == head
    assert "release-record" not in _calls(inst)


def test_s5_refuse_below_v110(inst):
    _installed(inst, "v1.1.1")
    code, out = _rollback(inst, "v1.0.0")
    _refused(inst, code, out, "ниже v1.1.0", "v1.1.1")


def test_s5_refuse_newer(inst):
    _installed(inst, "v1.1.0")
    code, out = _rollback(inst, "v1.1.1", LAST="v1.0.0 v1.1.0")
    _refused(inst, code, out, "новее установленной", "v1.1.0")


def test_s5_refuse_older_than_last_update_from(v112):
    _installed(v112, "v1.1.1", "v1.1.2")
    code, out = _rollback(v112, "v1.1.0", LAST="v1.1.1 v1.1.2")
    _refused(v112, code, out, "v1.1.0 старше v1.1.1", "v1.1.2")


def test_s5_refuse_no_rollback_in_changelog(v112):
    _installed(v112, "v1.1.2")
    code, out = _rollback(v112, "v1.1.1", LAST="v1.1.1 v1.1.2")
    _refused(v112, code, out, "у v1.1.2 в CHANGELOG.md «Откат: нет — 010 меняет смысл поля»", "v1.1.2")


def test_s5_refuse_no_update_in_journal(inst):
    _installed(inst, "v1.1.1")
    code, out = _rollback(inst, "v1.1.0", LAST="none")
    _refused(inst, code, out, "нет удачного обновления", "v1.1.1")


def test_s5_refuse_job_running(inst):
    _installed(inst, "v1.1.1")
    code, out = _rollback(inst, "v1.1.0", JOBS_RC="4")
    _refused(inst, code, out, "фоновая задача идёт", "v1.1.1")


def test_s5_refuse_manual_edits(inst):
    _installed(inst, "v1.1.1")
    (inst.root / "server/hotfix.py").write_text("x = 1\n", encoding="utf-8")
    code, out = _rollback(inst, "v1.1.0")
    _refused(inst, code, out, "ручные правки", "v1.1.1")


def test_s5_journal_unavailable_no_stop(inst):
    _installed(inst, "v1.1.1")
    code, out = _rollback(inst, "v1.1.0", RECORD_FAIL="1")
    assert code == 1 and "не удалось записать откат" in out
    assert _events(inst) == [] and inst.head() == "v1.1.1"


def test_s5_target_does_not_start_current_returns(inst):
    _installed(inst, "v1.1.1")
    code, out = _rollback(inst, "v1.1.0", BROKEN="v1.1.0")
    assert code == 1, out
    assert "не ответил версией v1.1.0" in out and "Откат не прошёл, работает v1.1.1, база не менялась" in out
    assert inst.head() == "v1.1.1"
    assert os.readlink(inst.root / "server/.venv") == ".venvs/v1.1.1"
    assert _events(inst) == ["stop", "start", "stop", "start"]
    calls = _calls(inst)
    assert "migrate" not in calls
    assert "release-record --kind auto_rollback --from v1.1.0 --to v1.1.1" in calls
