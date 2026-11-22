"""Поиск по смыслу: «схемотехник с Altium из приборостроения» (S31)."""
from __future__ import annotations

import hashlib
from datetime import datetime

import httpx
import psycopg

from . import embed, pipeline, scoring
from .clock import SYSTEM, Clock
from .db import ConnFactory, one, rows, run
from .jobs import JobStats


def similar(conn: psycopg.Connection, query: str, limit: int = 20, vec: list[float] | None = None) -> list[dict]:
    """Ближайшие по косинусу; только векторы текущего способа. vec — вектор запроса, если уже посчитан
    (API считает его через embed.tracked, чтобы отказ службы попал в ops.service_health)."""
    if not query.strip():
        raise ValueError("пустой запрос")
    vec = embed.literal(vec if vec is not None else embed.vector(query))
    return rows(conn, """
        SELECT p.id AS person_id, p.label, p.city, 1 - (e.embedding <=> %(v)s::vector) AS similarity
        FROM person_embedding e JOIN person p ON p.id = e.person_id
        WHERE e.embed_model = %(m)s
        ORDER BY e.embedding <=> %(v)s::vector LIMIT %(limit)s""",
                {"v": vec, "m": embed.method(), "limit": limit})


def reembed(conn_factory: ConnFactory, *, http: httpx.Client | None = None, only_stale: bool = False,
            clock: Clock | None = None, **_) -> JobStats:
    """Пересчёт векторов текущим способом. only_stale — только отсутствующие, другого способа или
    устаревшие (человек изменился позже вектора). Провайдер вызывается вне транзакции, пачками по
    embed_batch_size; пачка — своя транзакция."""
    stats = JobStats()
    model = embed.method()
    now = (clock or SYSTEM).now()
    with conn_factory() as conn:
        todo = rows(conn, """
            SELECT p.id FROM person p LEFT JOIN person_embedding e ON e.person_id = p.id
            WHERE NOT %(stale)s OR e.person_id IS NULL OR e.embed_model <> %(m)s
               OR e.updated_at < p.last_event_at
            ORDER BY p.id""", {"stale": only_stale, "m": model})
        texts = [(r["id"], pipeline.person_text(pipeline.person_data(conn, r["id"]))) for r in todo]
        size = embed.batch_size(conn)
    for i in range(0, len(texts), size):
        part = texts[i:i + size]
        stats.seen += len(part)
        try:
            vecs = embed.tracked(conn_factory, [t for _, t in part], http=http, now=now)
        except embed.ERRORS:
            stats.errors += len(part)
            continue
        with conn_factory() as conn:
            for (pid, _), vec in zip(part, vecs, strict=True):
                pipeline.write_embedding(conn, pid, vec, model, now)
        stats.changed += len(part)
    return stats


def profile_text(pr: dict) -> str:
    """Текст вектора профиля: название, ключи, синонимы, тексты must_have и nice_to_have."""
    p = scoring.Profile.from_row(pr)
    syn = [t for k in p.keywords for t in p.synonyms.get(scoring.norm_keyword(k), [])]
    reqs = [scoring.requirement_text(r) for r in p.must_have + p.nice_to_have]
    return ". ".join(filter(None, [p.title, ", ".join(p.keywords), ", ".join(syn), "; ".join(reqs)]))


def embed_profile(conn_factory: ConnFactory, role_profile_id: int, *, http: httpx.Client | None = None,
                  now: datetime | None = None) -> bool:
    """Вектор версии профиля в ops.profile_embedding — только если сменился текст или способ.
    True — пересчитан; отказ службы пробрасывается (embed.ERRORS), прежний вектор остаётся."""
    model = embed.method()
    with conn_factory() as conn:
        pr = one(conn, """SELECT rp.*, d.code AS dept_code FROM role_profile rp
                          JOIN department d ON d.id = rp.department_id WHERE rp.id = %s""", (role_profile_id,))
        if not pr:
            return False
        text = profile_text(pipeline.with_synonyms(conn, [pr])[0])
        sha = hashlib.sha256(text.encode()).hexdigest()
        cur = one(conn, "SELECT embed_model, text_sha256 FROM ops.profile_embedding WHERE role_profile_id = %s",
                  (role_profile_id,))
    if cur and cur["embed_model"] == model and cur["text_sha256"] == sha:
        return False
    vec = embed.tracked(conn_factory, [text], http=http, now=now)[0]
    with conn_factory() as conn:
        run(conn, """INSERT INTO ops.profile_embedding
                         (role_profile_id, embed_model, text_sha256, embedding, updated_at)
                     VALUES (%s, %s, %s, %s::vector, COALESCE(%s, now()))
                     ON CONFLICT (role_profile_id) DO UPDATE SET embed_model = EXCLUDED.embed_model,
                         text_sha256 = EXCLUDED.text_sha256, embedding = EXCLUDED.embedding,
                         updated_at = EXCLUDED.updated_at""",
            (role_profile_id, model, sha, embed.literal(vec), now))
    return True


def refresh_profile(conn_factory: ConnFactory, role_profile_id: int) -> None:
    """Фоновый пересчёт вектора профиля после утверждения версии, правки и отката синонимов.
    Отказ службы уже записан в ops.service_health — вектор досчитает ночная задача (S8, S10)."""
    try:
        embed_profile(conn_factory, role_profile_id)
    except embed.ERRORS:
        pass
