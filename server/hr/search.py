"""Поиск по смыслу: «схемотехник с Altium из приборостроения» (S31)."""
from __future__ import annotations

import httpx
import psycopg

from . import embed, pipeline
from .clock import SYSTEM, Clock
from .db import ConnFactory, rows
from .jobs import JobStats


def similar(conn: psycopg.Connection, query: str, limit: int = 20) -> list[dict]:
    """Ближайшие по косинусу; только векторы текущего способа."""
    if not query.strip():
        raise ValueError("пустой запрос")
    vec = embed.literal(embed.vector(query))
    return rows(conn, """
        SELECT p.id AS person_id, p.label, p.city, 1 - (e.embedding <=> %(v)s::vector) AS similarity
        FROM person_embedding e JOIN person p ON p.id = e.person_id
        WHERE e.embed_model = %(m)s
        ORDER BY e.embedding <=> %(v)s::vector LIMIT %(limit)s""",
                {"v": vec, "m": embed.method(), "limit": limit})


def reembed(conn_factory: ConnFactory, *, http: httpx.Client | None = None, only_stale: bool = False,
            clock: Clock | None = None, **_) -> JobStats:
    """Пересчёт векторов текущим способом. only_stale — только отсутствующие, другого способа или
    устаревшие (человек изменился позже вектора). Провайдер вызывается вне транзакции."""
    stats = JobStats()
    model = embed.method()
    with conn_factory() as conn:
        todo = rows(conn, """
            SELECT p.id FROM person p LEFT JOIN person_embedding e ON e.person_id = p.id
            WHERE NOT %(stale)s OR e.person_id IS NULL OR e.embed_model <> %(m)s
               OR e.updated_at < p.last_event_at
            ORDER BY p.id""", {"stale": only_stale, "m": model})
        texts = [(r["id"], pipeline.person_text(pipeline.person_data(conn, r["id"]))) for r in todo]
    for pid, text in texts:
        stats.seen += 1
        try:
            vec = embed.vector(text, http)
        except (httpx.HTTPError, ValueError, KeyError):
            stats.errors += 1
            continue
        with conn_factory() as conn:
            pipeline.write_embedding(conn, pid, vec, model, (clock or SYSTEM).now())
        stats.changed += 1
    return stats
