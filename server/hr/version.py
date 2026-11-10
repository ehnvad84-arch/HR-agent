"""Версия кода и базы (S1): номер из VERSION, дата из CHANGELOG, миграции применённые и нет."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import psycopg

from . import db

ROOT = Path(__file__).resolve().parents[2]
_TAG = re.compile(r"^v\d+\.\d+\.\d+$")


def code_version(root: Path | None = None) -> str:
    """VERSION в корне; без него (установка до v1.1.0) — тег текущего коммита, иначе `unknown`."""
    root = root or ROOT
    f = root / "VERSION"
    if f.is_file():
        v = f.read_text(encoding="utf-8").strip()
        return v or "unknown"
    try:
        out = subprocess.run(["git", "-C", str(root), "describe", "--tags", "--exact-match", "HEAD"],  # noqa: S603, S607
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    tag = out.stdout.strip()
    return tag if out.returncode == 0 and _TAG.match(tag) else "unknown"


def release_date(version: str | None = None, root: Path | None = None) -> str | None:
    """Дата из заголовка `## vX.Y.Z — ГГГГ-ММ-ДД` в CHANGELOG.md; нет раздела — None."""
    root = root or ROOT
    version = version or code_version(root)
    f = root / "CHANGELOG.md"
    if not f.is_file():
        return None
    m = re.search(rf"^## {re.escape(version)} — (\d{{4}}-\d{{2}}-\d{{2}})\s*$", f.read_text(encoding="utf-8"),
                  re.MULTILINE)
    return m.group(1) if m else None


def status(conn: psycopg.Connection, root: Path | None = None) -> dict:
    """code — версия кода; db — последняя удачная запись журнала версий; applied — сколько миграций применено;
    pending — что применит migrate; ahead — применённые, которых нет в коде (норма после отката, S5)."""
    last = db.one(conn, """SELECT to_version FROM ops.release WHERE ok
                           ORDER BY coalesce(finished_at, started_at) DESC, id DESC LIMIT 1""") \
        if db.val(conn, "SELECT to_regclass('ops.release') IS NOT NULL") else None
    done = db.applied(conn)
    files = {f.name for f in db.migration_files()}
    return {"code": code_version(root), "db": last["to_version"] if last else None,
            "applied": len(done), "pending": db.pending(conn), "ahead": sorted(done - files)}
