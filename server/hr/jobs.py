"""Запуск фоновой задачи: журнал ops.job_run, защита от параллельного запуска, классы ошибок.

Контракт задачи: func(conn_factory, *, http=None, clock=None, **kw) -> JobStats.
Планировщик (фича deploy) повторяет только непредвиденные ошибки: на капчу,
отсутствие настроек и занятость повтор ничего не исправит.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from typing import Any

import psycopg

from . import db
from .config import get_config

log = logging.getLogger(__name__)


class JobError(RuntimeError):
    """Ошибка с понятным человеку текстом: он попадает в журнал как есть."""


class JobBlocked(JobError):
    """Площадка просит пройти проверку на робота или ограничила частоту — обходить не будем."""


class JobLimit(JobError):
    """Суточный лимит площадки исчерпан — продолжим в следующий прогон, повтор сейчас не поможет."""


class JobNotConfigured(JobError):
    """Нет токена, ключа, источника или эндпойнта."""


class JobBusy(JobError):
    """Эта задача или задача той же площадки уже идёт."""


@dataclass
class JobStats:
    seen: int = 0
    new: int = 0
    changed: int = 0
    persons: int = 0
    alerts: int = 0
    skipped: int = 0
    errors: int = 0
    pages: int = 0
    limit_reached: bool = False
    note: str | None = None
    extra: dict = field(default_factory=dict)

    def add(self, other: JobStats) -> None:
        for k in ("seen", "new", "changed", "persons", "alerts", "skipped", "errors", "pages"):
            setattr(self, k, getattr(self, k) + getattr(other, k))
        self.limit_reached = self.limit_reached or other.limit_reached


@dataclass
class JobResult:
    ok: bool
    stats: JobStats
    error: str | None = None
    exc: BaseException | None = None

    @property
    def retry(self) -> bool:
        return not self.ok and not isinstance(self.exc, JobError)


# идущий прогон: фабрика соединений, id строки ops.job_run, время последней записи хода
_current: ContextVar[list | None] = ContextVar("job_run", default=None)


def progress(force: bool = False, **fields: Any) -> None:
    """Ход задачи для полосы загрузки (S48): поля сливаются в ops.job_run.progress, не чаще раза в секунду.
    Вне jobs.run ничего не делает. Только числа и служебный текст — без данных о людях."""
    cur = _current.get()
    if cur is None:
        return
    factory, run_id, last, _ = cur
    now = time.monotonic()
    if not force and now - last < 1.0:
        cur[3].update(fields)   # отложенные поля уйдут со следующей записью
        return
    payload = {**cur[3], **fields}
    cur[2], cur[3] = now, {}
    try:
        with factory() as conn:
            db.run(conn, "UPDATE ops.job_run SET progress = progress || %s WHERE id = %s", (db.j(payload), run_id))
    except psycopg.Error:
        log.warning("ход задачи не записан")


def _key(name: str) -> str:
    return f"job:{name}"


@contextmanager
def try_lock(key: str) -> Iterator[bool]:
    """Та же блокировка, что у задач (например, source:hh): True — взяли, False — занята."""
    conn = psycopg.connect(get_config().database_url, autocommit=True)
    try:
        yield conn.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (key,)).fetchone()[0]
    finally:
        conn.close()   # закрытие соединения снимает блокировку


def run(name: str, func: Callable[..., JobStats], conn_factory: db.ConnFactory | None,
        locks: tuple[str, ...] = (), **kw: Any) -> JobResult:
    """Запускает задачу. locks — общие блокировки, например ('source:hh',) у задач одной площадки."""
    factory = conn_factory or db.tx
    lock_conn = psycopg.connect(get_config().database_url, autocommit=True)
    try:
        for key in (_key(name), *locks):
            got = lock_conn.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (key,)).fetchone()[0]
            if not got:
                return _journal(factory, name, JobResult(False, JobStats(), "уже идёт", JobBusy(key)))
        with factory() as conn:
            run_id = db.val(conn, "INSERT INTO ops.job_run (job) VALUES (%s) RETURNING id", (name,))
        token = _current.set([factory, run_id, 0.0, {}])
        try:
            stats = func(factory, **kw) or JobStats()
            err = f"{stats.errors} записей не принято" if stats.errors else None
            result = JobResult(True, stats, err)
        except JobError as e:
            result = JobResult(False, JobStats(), str(e), e)
        except Exception as e:  # noqa: BLE001 — непредвиденное: в журнал только класс, без данных
            log.exception("задача %s упала", name)
            result = JobResult(False, JobStats(), type(e).__name__, e)
        finally:
            _current.reset(token)
        out = _journal(factory, name, result, run_id)
        from . import load_check  # S20: итог загрузки — отделу персонала (импорт здесь: load_check знает jobs)
        load_check.after_run(factory, name, out, run_id)
        return out
    finally:
        lock_conn.close()   # закрытие соединения снимает advisory-блокировки


def _journal(factory: db.ConnFactory, name: str, res: JobResult, run_id: int | None = None) -> JobResult:
    stats = asdict(res.stats)
    if isinstance(res.exc, JobBlocked):
        stats["blocked"] = True
    with factory() as conn:
        if run_id is None:
            db.run(conn, """INSERT INTO ops.job_run (job, finished_at, ok, stats, error)
                            VALUES (%s, now(), %s, %s, %s)""", (name, res.ok, db.j(stats), res.error))
        else:
            db.run(conn, "UPDATE ops.job_run SET finished_at = now(), ok = %s, stats = %s, error = %s WHERE id = %s",
                   (res.ok, db.j(stats), res.error, run_id))
    return res
