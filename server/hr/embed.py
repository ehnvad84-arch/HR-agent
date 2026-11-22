"""Векторы для поиска по смыслу (person_embedding, vector(1024), S31).

Способ записывается в person_embedding.embed_model: векторы разных способов
несравнимы, поиск идёт только по текущему. Без провайдера — hash-v1: хеширование
основ слов и триграмм в коде, без внешних сервисов. Смена способа — hr.cli reembed.
"""
from __future__ import annotations

import hashlib
import math
import re
from datetime import UTC, datetime

import httpx
import psycopg

from .config import get_config
from .db import ConnFactory, run, setting, val

DIM = 1024
HASH = "hash-v1"


def method() -> str:
    cfg = get_config()
    return cfg.embed_model if cfg.embed_base_url and cfg.embed_model else HASH


def hash_vector(text: str) -> list[float]:
    v = [0.0] * DIM
    for w in re.findall(r"\w+", text.lower()):
        stem = w[:max(4, len(w) - 3)] if len(w) > 6 else w
        feats = [("w:" + stem, 1.0)] + [("t:" + f"_{w}_"[i:i + 3], 0.3) for i in range(len(w))]
        for f, weight in feats:
            h = int.from_bytes(hashlib.blake2b(f.encode(), digest_size=8).digest(), "little")
            v[h % DIM] += weight if (h >> 63) & 1 else -weight
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / norm for x in v]


ERRORS = (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError)   # отказ службы векторов (S8)
SERVICE = "embed"


def client() -> httpx.Client:
    return httpx.Client(timeout=get_config().embed_timeout_s)


def _clean(text: str) -> str:
    """Контакты в службу векторов не уходят (S9); импорт здесь — learning сам зависит от embed."""
    from .learning import strip_contacts

    return strip_contacts(text)[:8000]


def api_vectors(texts: list[str], http: httpx.Client | None = None) -> list[list[float]]:
    """Один запрос /embeddings на пачку input: [...]; порядок — по index ответа."""
    cfg = get_config()
    headers = {"Authorization": f"Bearer {cfg.embed_api_key}"} if cfg.embed_api_key else {}
    r = (http or client()).post(cfg.embed_base_url.rstrip("/") + "/embeddings",
                                json={"model": cfg.embed_model, "input": [_clean(t) for t in texts]}, headers=headers)
    r.raise_for_status()
    data = sorted(r.json()["data"], key=lambda d: d.get("index", 0))
    if len(data) != len(texts):
        raise ValueError(f"модель эмбеддингов вернула {len(data)} векторов на {len(texts)} текстов")
    out = [d["embedding"] for d in data]
    for vec in out:
        if len(vec) != DIM:
            raise ValueError(f"модель эмбеддингов отдаёт {len(vec)} измерений, схема ждёт {DIM}")
    return out


def vectors(texts: list[str], http: httpx.Client | None = None, batch_size: int = 32) -> list[list[float]]:
    """Векторы текстов текущим способом; у службы — пачками по batch_size за запрос."""
    if method() == HASH:
        return [hash_vector(t) for t in texts]
    step = max(1, int(batch_size))
    out: list[list[float]] = []
    for i in range(0, len(texts), step):
        out += api_vectors(texts[i:i + step], http)
    return out


def vector(text: str, http: httpx.Client | None = None) -> list[float]:
    return vectors([text], http)[0]


def batch_size(conn: psycopg.Connection) -> int:
    return int(setting(conn, "embed_batch_size", 32) or 32)


def tracked(conn_factory: ConnFactory, texts: list[str], *, http: httpx.Client | None = None,
            now: datetime | None = None) -> list[list[float]]:
    """vectors с пачкой из настройки и отметкой в ops.service_health (S8): первый отказ ставит down_since,
    успех снимает. Служба вызывается вне транзакции; отказ пробрасывается (ERRORS)."""
    if method() == HASH:
        return vectors(texts)
    now = now or datetime.now(UTC)
    with conn_factory() as conn:
        size = batch_size(conn)
    try:
        out = vectors(texts, http, size)
    except ERRORS as e:
        mark_down(conn_factory, now, e)
        raise
    mark_ok(conn_factory, now)
    return out


def mark_ok(conn_factory: ConnFactory, now: datetime) -> None:
    with conn_factory() as conn:
        run(conn, """INSERT INTO ops.service_health (service, down_since, last_ok_at) VALUES (%s, NULL, %s)
                     ON CONFLICT (service) DO UPDATE SET down_since = NULL, last_ok_at = EXCLUDED.last_ok_at""",
            (SERVICE, now))


def mark_down(conn_factory: ConnFactory, now: datetime, exc: BaseException) -> None:
    """В last_error — только тип исключения: в тексте ответа могут быть данные резюме."""
    with conn_factory() as conn:
        run(conn, """INSERT INTO ops.service_health (service, down_since, last_error) VALUES (%s, %s, %s)
                     ON CONFLICT (service) DO UPDATE SET last_error = EXCLUDED.last_error,
                         down_since = COALESCE(ops.service_health.down_since, EXCLUDED.down_since)""",
            (SERVICE, now, type(exc).__name__))


def down_since(conn: psycopg.Connection) -> datetime | None:
    """С какого момента служба векторов недоступна; None — работает или не подключена."""
    if method() == HASH:
        return None
    return val(conn, "SELECT down_since FROM ops.service_health WHERE service = %s", (SERVICE,))


def literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"
