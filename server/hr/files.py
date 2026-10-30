"""Выгрузки из кабинетов площадок без API (раздел 5.3 ТЗ, S7, S8).

Файлы *.csv и *.xlsx кладутся в IMPORT_DIR. Источник — начало имени до «_»
(habr_2026-10-25.csv → Хабр Карьера), иначе ручной ввод. Принятый файл уходит в
done/, нечитаемый — в error/; к имени добавляется время, прежние не затираются.
"""
from __future__ import annotations

import csv
import hashlib
import io
import logging
import shutil
from datetime import datetime
from pathlib import Path

from openpyxl import load_workbook

from . import pipeline
from .clock import SYSTEM, Clock
from .config import get_config
from .db import ConnFactory, j, one, run, setting
from .jobs import JobStats
from .normalize import normalize

log = logging.getLogger(__name__)

SUFFIXES = {".csv", ".xlsx"}


class Rejected(ValueError):
    """Файл целиком не принимается: формат, размер, число строк."""


def _text(raw: bytes) -> str:
    for enc in ("utf-8-sig", "cp1251"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise Rejected("кодировка не распознана")


def read_rows(path: Path, max_rows: int) -> list[dict]:
    if path.suffix.lower() == ".csv":
        text = _text(path.read_bytes())
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=";,\t")
        except csv.Error:
            dialect = csv.excel
        rows = list(csv.DictReader(io.StringIO(text), dialect=dialect))
    else:
        try:
            wb = load_workbook(path, read_only=True, data_only=True)   # openpyxl разбирает XML через defusedxml
        except Exception as e:  # noqa: BLE001 — любой битый файл одинаково уходит в error/
            raise Rejected(f"файл не читается как xlsx: {type(e).__name__}") from None
        it = wb.active.iter_rows(values_only=True)
        head = [str(h).strip() if h is not None else "" for h in next(it, [])]
        rows = [dict(zip(head, r, strict=False)) for r in it if any(v not in (None, "") for v in r)]
        wb.close()
    if len(rows) > max_rows:
        raise Rejected(f"строк {len(rows)}, предел {max_rows}")
    return rows


def _move(path: Path, folder: Path, now: datetime) -> Path:
    folder.mkdir(exist_ok=True)
    stem = f"{now:%Y%m%d-%H%M%S}_{path.name}"
    dest, n = folder / stem, 1
    while dest.exists():
        dest, n = folder / f"{now:%Y%m%d-%H%M%S}-{n}_{path.name}", n + 1
    shutil.move(str(path), dest)
    return dest


def _source_code(conn, name: str) -> str:
    prefix = name.split("_", 1)[0].lower()
    return prefix if "_" in name and one(conn, "SELECT 1 FROM source WHERE code = %s", (prefix,)) else "manual"


def import_files(conn_factory: ConnFactory, *, folder: str | None = None, clock: Clock | None = None,
                 **_) -> JobStats:
    clock = clock or SYSTEM
    root = Path(folder or get_config().import_dir)
    root.mkdir(parents=True, exist_ok=True)
    stats = JobStats()
    with conn_factory() as conn:
        max_mb = float(setting(conn, "import_max_file_mb", 20))
        max_rows = int(setting(conn, "import_max_rows", 10000))
    for path in sorted(root.iterdir()):
        if path.is_symlink() or not path.is_file() or path.suffix.lower() not in SUFFIXES:
            continue
        now = clock.now()
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        with conn_factory() as conn:
            if one(conn, "SELECT 1 FROM ops.import_file WHERE sha256 = %s", (sha,)):
                stats.skipped += 1
                _move(path, root / "done", now)
                continue
            code = _source_code(conn, path.name)
            src = one(conn, "SELECT * FROM source WHERE code = %s", (code,))
        file_stats = JobStats()
        try:
            if path.stat().st_size > max_mb * 1024 * 1024:
                raise Rejected(f"больше {max_mb:g} МБ")
            for i, row in enumerate(read_rows(path, max_rows), start=2):
                file_stats.seen += 1
                try:
                    with conn_factory() as conn:
                        r = pipeline.process(conn, src, normalize(code, row), row, now)
                    file_stats.new += int(r.new)
                    file_stats.changed += int(r.changed)
                    file_stats.persons += int(r.person_created)
                    file_stats.alerts += r.alerts
                except Exception:  # noqa: BLE001 — битая строка не роняет файл
                    log.warning("файл %s: строка %s не принята", path.name, i)
                    file_stats.errors += 1
            state, error, dest = "applied", None, root / "done"
        except Rejected as e:
            state, error, dest = "rejected", str(e), root / "error"
            file_stats.errors += 1
        with conn_factory() as conn:
            run(conn, """INSERT INTO ops.import_file (file_name, sha256, source_code, received_at, state, stats, error)
                         VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (path.name, sha, code, now, state, j({"seen": file_stats.seen, "new": file_stats.new,
                                                     "errors": file_stats.errors}), error))
        _move(path, dest, now)
        stats.add(file_stats)
    return stats
