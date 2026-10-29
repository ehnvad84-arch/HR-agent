"""Фото из резюме: скачать один раз, сжать до photo_max_bytes, хранить у себя (S9; решение человека 2026-10-29).

Только хосты hh, ответ не больше 2 МБ, не чаще запроса в секунду (never.md), не больше трёх попыток.
EXIF и прочие метаданные удаляются: в файле остаются только пиксели.
"""
from __future__ import annotations

import hashlib
import io
import logging
from urllib.parse import urlparse

import httpx
from PIL import Image

from .clock import SYSTEM, Clock
from .db import ConnFactory, rows, run, setting
from .http import RateLimiter, client
from .jobs import JobStats

log = logging.getLogger(__name__)
HOSTS = ("hh.ru", "hhcdn.ru")
MAX_DOWNLOAD = 2 * 1024 * 1024
MAX_ATTEMPTS = 3
Image.MAX_IMAGE_PIXELS = 25_000_000   # «бомба распаковки» — отказ, а не память сервера

_limiter = RateLimiter()


def allowed(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return urlparse(url).scheme == "https" and any(host == h or host.endswith("." + h) for h in HOSTS)


def photo_url(raw: dict) -> str | None:
    p = raw.get("photo") if isinstance(raw, dict) else None
    if not isinstance(p, dict):
        return None
    return p.get("medium") or p.get("small") or p.get("40")


def compress(data: bytes, limit: int) -> bytes:
    """JPEG не больше limit байт: уменьшаем сторону и качество, пока не влезет."""
    img = Image.open(io.BytesIO(data))
    img.load()
    img = img.convert("RGB")
    for side in (320, 240, 180, 128, 96, 64):
        small = img.copy()
        small.thumbnail((side, side))
        for q in (80, 70, 60, 50, 40, 30):
            buf = io.BytesIO()
            small.save(buf, "JPEG", quality=q, optimize=True)   # без exif=: метаданные не переносятся
            if buf.tell() <= limit:
                return buf.getvalue()
    raise ValueError("фото не сжимается до предела")


def _download(http: httpx.Client, url: str) -> bytes:
    with http.stream("GET", url, timeout=10.0) as r:
        r.raise_for_status()
        out = bytearray()
        for chunk in r.iter_bytes():
            out += chunk
            if len(out) > MAX_DOWNLOAD:
                raise ValueError("фото больше 2 МБ")
        return bytes(out)


def _save(conn_factory: ConnFactory, oid: int, state: str, data: bytes | None = None) -> None:
    with conn_factory() as conn:
        run(conn, """INSERT INTO ops.person_photo (observation_id, data, sha256, state, attempts, fetched_at)
                     VALUES (%s, %s, %s, %s, 1, now())
                     ON CONFLICT (observation_id) DO UPDATE SET data = EXCLUDED.data, sha256 = EXCLUDED.sha256,
                         state = EXCLUDED.state, attempts = ops.person_photo.attempts + 1, fetched_at = now()""",
            (oid, data, hashlib.sha256(data).hexdigest() if data else None, state))


def fetch_photos(conn_factory: ConnFactory, *, http: httpx.Client | None = None, clock: Clock | None = None,
                 limiter: RateLimiter | None = None, **_) -> JobStats:
    """Задача photos: фото наблюдений с карточкой, у которых его ещё нет."""
    clock, limiter = clock or SYSTEM, limiter or _limiter
    http = http or client()
    stats = JobStats()
    with conn_factory() as conn:
        limit = int(setting(conn, "photo_max_bytes", 10240))
        todo = rows(conn, """
            SELECT o.id, o.raw->'photo' AS photo FROM observation o
            LEFT JOIN ops.person_photo ph ON ph.observation_id = o.id
            WHERE o.person_id IS NOT NULL AND NOT o.is_gone
              AND (ph.observation_id IS NULL OR (ph.state = 'failed' AND ph.attempts < %s))
              AND (ph.state IS NULL OR ph.state <> 'absent')
            ORDER BY o.last_seen DESC""", (MAX_ATTEMPTS,))
    for o in todo:
        stats.seen += 1
        url = photo_url({"photo": o["photo"]})
        if not url:
            _save(conn_factory, o["id"], "absent")
            continue
        if not allowed(url):
            with conn_factory() as conn:   # чужой хост не исправится повтором: сразу последняя попытка
                run(conn, """INSERT INTO ops.person_photo (observation_id, state, attempts) VALUES (%s, 'failed', %s)
                             ON CONFLICT (observation_id) DO UPDATE SET state = 'failed', attempts = %s""",
                    (o["id"], MAX_ATTEMPTS, MAX_ATTEMPTS))
            stats.skipped += 1
            continue
        limiter.wait("hh")
        try:
            data = compress(_download(http, url), limit)
        except (httpx.HTTPError, ValueError, OSError, Image.DecompressionBombError) as e:
            log.warning("фото не получено: %s", type(e).__name__)
            _save(conn_factory, o["id"], "failed")
            stats.errors += 1
            continue
        _save(conn_factory, o["id"], "ok", data)
        stats.new += 1
    return stats


def photo_of(conn, person_id: int) -> bytes | None:
    r = rows(conn, """SELECT ph.data FROM ops.person_photo ph JOIN observation o ON o.id = ph.observation_id
                      WHERE o.person_id = %s AND ph.state = 'ok' ORDER BY o.last_seen DESC LIMIT 1""", (person_id,))
    return bytes(r[0]["data"]) if r else None
