"""Журнал версий и проверки для update.sh: идущие задачи, недостающие переменные .env, итог копии базы."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import psycopg

from . import db

SERVER = Path(__file__).resolve().parents[1]
KINDS = ("update", "auto_rollback", "rollback", "baseline")
_ENV_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


def jobs_running(conn: psycopg.Connection) -> tuple[list[dict], list[dict]]:
    """Незавершённые прогоны ops.job_run: (идут — моложе load_stuck_hours, зависшие — старше)."""
    hours = float(db.setting(conn, "load_stuck_hours", 3))
    found = db.rows(conn, """SELECT id, job, started_at, started_at < now() - %s * interval '1 hour' AS stuck
                             FROM ops.job_run WHERE finished_at IS NULL ORDER BY started_at""", (hours,))
    return [r for r in found if not r["stuck"]], [r for r in found if r["stuck"]]


def record(conn: psycopg.Connection, kind: str, to_version: str, *, from_version: str | None = None,
           git_commit: str | None = None, migrations: list[str] | None = None, actor: str | None = None,
           ok: bool = True, note: str | None = None) -> int:
    """Строка ops.release (S2, S4, S5): обновление, автоматический возврат, откат или исходная точка."""
    if kind not in KINDS:
        raise ValueError(f"вид записи — один из {', '.join(KINDS)}")
    return db.val(conn, """INSERT INTO ops.release (kind, from_version, to_version, git_commit, migrations, actor,
                                                    finished_at, ok, note)
                           VALUES (%s, %s, %s, %s, %s, %s, now(), %s, %s) RETURNING id""",
                  (kind, from_version, to_version, git_commit, migrations or [], actor, ok, note))


def notices(conn: psycopg.Connection, migrations: list[str]) -> list[str]:
    """Что сказали миграции этого прогона: «настройка X оставлена как есть» (S8)."""
    if not migrations or not db.val(conn, "SELECT to_regclass('ops.release_notice') IS NOT NULL"):
        return []
    return [r["message"] for r in db.rows(conn, """SELECT message FROM ops.release_notice
                                                   WHERE migration = ANY(%s) ORDER BY id""", (migrations,))]


def _env_names(path: Path) -> dict[str, bool]:
    """Имя переменной → задано ли непустое значение. Значения дальше не идут."""
    out: dict[str, bool] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _ENV_LINE.match(line)
        if m:
            out[m.group(1)] = bool(m.group(2).strip().strip("'\""))
    return out


def env_missing(example: Path | None = None, env: Path | None = None) -> list[str]:
    """Имена из .env.example, которых нет в .env или они пустые (S9). Значения не возвращаются."""
    example = example or SERVER / ".env.example"
    env = env or SERVER / ".env"
    have = _env_names(env) if env.is_file() else {}
    return [name for name in _env_names(example) if not have.get(name)]


def job_record(conn: psycopg.Connection, job: str, ok: bool, error: str | None = None,
               stats: dict[str, Any] | None = None) -> int:
    """Итог задачи, которая идёт вне hr (копия базы db_copy): строка ops.job_run, видна в «Схеме»."""
    return db.val(conn, """INSERT INTO ops.job_run (job, finished_at, ok, stats, error)
                           VALUES (%s, now(), %s, %s, %s) RETURNING id""", (job, ok, db.j(stats or {}), error))
