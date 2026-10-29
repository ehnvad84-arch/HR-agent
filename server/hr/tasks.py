"""Реестр фоновых задач core и workplace. Имена — из раздела 8 ТЗ; расписание задаёт фича deploy."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from . import alerts, collect, explain, files, jobs, photos, pipeline, search, social
from .clock import SYSTEM, Clock
from .db import ConnFactory, rows
from .db import run as sql
from .jobs import JobStats


def score(conn_factory: ConnFactory, *, http=None, clock: Clock | None = None, **_) -> JobStats:
    """06:50: объяснения моделью для пар выше порога и досчёт векторов (S16, S19, S31)."""
    stats = explain.explain_pending(conn_factory, http=http)
    vec = search.reembed(conn_factory, only_stale=True, clock=clock)
    stats.extra = {"vectors": vec.changed, "vector_errors": vec.errors}
    return stats


def alerts_pass(conn_factory: ConnFactory, *, clock: Clock | None = None, **_) -> JobStats:
    """06:58: страховочный проход по событиям, которые не прошли через конвейер (например, disappeared)."""
    now = (clock or SYSTEM).now()
    stats = JobStats()
    with conn_factory() as conn:
        pids = [r["person_id"] for r in rows(conn, """SELECT DISTINCT person_id FROM obs_event
                                                       WHERE NOT processed AND person_id IS NOT NULL""")]
    for pid in pids:
        with conn_factory() as conn:
            evs = rows(conn, """SELECT id, kind, detail FROM obs_event WHERE person_id = %s AND NOT processed
                                ORDER BY happened_at FOR UPDATE""", (pid,))
            scores = pipeline.rescore_person(conn, pid)
            stats.alerts += alerts.evaluate(conn, pid, [(e["kind"], e["detail"]) for e in evs], scores, now)
            sql(conn, "UPDATE obs_event SET processed = true WHERE id = ANY(%s)", ([e["id"] for e in evs],))
        stats.seen += 1
    return stats


@dataclass(frozen=True)
class Task:
    func: Callable[..., JobStats]
    locks: tuple[str, ...] = ()


TASKS: dict[str, Task] = {
    "collect_hh": Task(collect.collect_hh, ("source:hh",)),
    "import_files": Task(files.import_files),
    "mark_gone": Task(collect.mark_gone, ("source:hh",)),
    "score": Task(score),
    "alerts": Task(alerts_pass),
    "collect_social": Task(social.collect_social, ("source:github",)),
    "photos": Task(photos.fetch_photos, ("source:hh",)),
}


def run(name: str, conn_factory: ConnFactory | None = None, **kw) -> jobs.JobResult:
    t = TASKS[name]
    return jobs.run(name, t.func, conn_factory, locks=t.locks, **kw)
