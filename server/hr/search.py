"""Поиск по смыслу: «схемотехник с Altium из приборостроения» (S31)."""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import psycopg

from . import embed, pipeline, scoring
from .clock import SYSTEM, Clock
from .db import ConnFactory, one, rows, run, setting, val
from .jobs import JobStats

MSK = ZoneInfo("Europe/Moscow")


def similar(conn: psycopg.Connection, query: str, limit: int = 20, vec: list[float] | None = None) -> list[dict]:
    """Ближайшие по косинусу; только векторы текущего способа. vec — вектор запроса, если уже посчитан
    (API считает его через embed.tracked, чтобы отказ службы попал в ops.service_health)."""
    if not query.strip():
        raise ValueError("пустой запрос")
    vec = embed.literal(vec if vec is not None else embed.vector(query))
    _full_scan(conn)
    return rows(conn, """
        SELECT p.id AS person_id, p.label, p.city, 1 - (e.embedding <=> %(v)s::vector) AS similarity
        FROM person_embedding e JOIN person p ON p.id = e.person_id
        WHERE e.embed_model = %(m)s
        ORDER BY e.embedding <=> %(v)s::vector LIMIT %(limit)s""",
                {"v": vec, "m": embed.method(), "limit": limit})


QUERY_MAX = 300   # знаков в запросе поиска по смыслу


def _full_scan(conn: psycopg.Connection) -> None:
    """При смеси векторов разных моделей HNSW отдаёт ef_search ближайших до фильтра embed_model — выдача
    могла бы оказаться короче limit. pgvector ≥ 0.8 продолжает обход (iterative_scan), раньше — шире ef_search.
    Действует до конца транзакции."""
    ver = val(conn, "SELECT extversion FROM pg_extension WHERE extname = 'vector'") or "0"
    if tuple(int(x) for x in ver.split(".")[:2]) >= (0, 8):
        run(conn, "SELECT set_config('hnsw.iterative_scan', 'strict_order', true)")
    run(conn, "SELECT set_config('hnsw.ef_search', '200', true)")


def missing_count(conn: psycopg.Connection, model: str) -> int:
    """Люди без вектора текущей модели — не участвуют в поиске и оцениваются без смысла (S10)."""
    return val(conn, """SELECT count(*) FROM person p
                        WHERE NOT EXISTS (SELECT 1 FROM person_embedding e
                                          WHERE e.person_id = p.id AND e.embed_model = %s)""", (model,))


def pending_people(conn: psycopg.Connection, model: str, only_stale: bool = True) -> list[int]:
    """Люди к пересчёту: сначала без вектора текущего способа, потом устаревшие (человек изменился позже
    вектора). only_stale=False — все люди."""
    return [r["id"] for r in rows(conn, """
        SELECT p.id FROM person p LEFT JOIN person_embedding e ON e.person_id = p.id
        WHERE NOT %(stale)s OR e.person_id IS NULL OR e.embed_model <> %(m)s
           OR e.updated_at < p.last_event_at
        ORDER BY (e.person_id IS NOT NULL AND e.embed_model = %(m)s), p.id""",
                                  {"stale": only_stale, "m": model})]


def _batch(conn_factory: ConnFactory, ids: list[int], model: str, http: httpx.Client | None,
           now: datetime) -> None:
    """Пачка: векторы (служба — вне транзакции), затем в одной транзакции запись и пересчёт балла её людей.
    Пересчёт — rescore_person: событий и сигналов не создаёт, «мой балл» пересчитывает. Отказ — embed.ERRORS."""
    with conn_factory() as conn:
        texts = [pipeline.person_text(pipeline.person_data(conn, pid)) for pid in ids]
    vecs = embed.tracked(conn_factory, texts, http=http, now=now)
    with conn_factory() as conn:
        profiles = pipeline.current_profiles(conn)
        for pid, vec in zip(ids, vecs, strict=True):
            pipeline.write_embedding(conn, pid, vec, model, now)
            pipeline.rescore_person(conn, pid, profiles)


def reembed(conn_factory: ConnFactory, *, http: httpx.Client | None = None, only_stale: bool = False,
            clock: Clock | None = None, max_batches: int | None = None, **_) -> JobStats:
    """Пересчёт векторов текущим способом пачками по embed_batch_size; пачка — своя транзакция с пересчётом
    балла. only_stale — только отсутствующие, другого способа или устаревшие. max_batches — предел пачек
    (задача score берёт одну, чтобы утром не занять процессор; остальное — reembed_night)."""
    stats = JobStats()
    model = embed.method()
    now = (clock or SYSTEM).now()
    with conn_factory() as conn:
        todo = pending_people(conn, model, only_stale)
        size = embed.batch_size(conn)
    for n, i in enumerate(range(0, len(todo), size)):
        if max_batches is not None and n >= max_batches:
            break
        part = todo[i:i + size]
        stats.seen += len(part)
        try:
            _batch(conn_factory, part, model, http, now)
        except embed.ERRORS:
            stats.errors += len(part)
            continue
        stats.changed += len(part)
    return stats


NIGHT_FAILS = 3   # отказов подряд — пересчёт до следующей ночи (S10)


def night_deadline(conn: psycopg.Connection, now: datetime) -> datetime:
    """Ближайшее embed_night_until («06:30» по Москве) после now."""
    hh, mm = (int(x) for x in str(setting(conn, "embed_night_until", "06:30")).split(":"))
    local = now.astimezone(MSK)
    end = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return end if end > local else end + timedelta(days=1)


def reembed_night(conn_factory: ConnFactory, *, http: httpx.Client | None = None, clock: Clock | None = None,
                  **_) -> JobStats:
    """Задача reembed_night (S10): векторы профилей → люди без вектора текущего способа → устаревшие.
    Пачка — своя транзакция с пересчётом балла; остановка в embed_night_until или после трёх отказов подряд.
    Прерванный пересчёт продолжается следующей ночью: список берётся заново по состоянию базы."""
    clock = clock or SYSTEM
    stats = JobStats()
    model = embed.method()
    with conn_factory() as conn:
        end = night_deadline(conn, clock.now())
        profile_ids = [p["id"] for p in pipeline.current_profiles(conn)]
    fails = 0

    def stop() -> bool:
        if fails >= NIGHT_FAILS:
            stats.note = f"служба векторов не отвечает: {NIGHT_FAILS} отказа подряд — продолжим следующей ночью"
            return True
        if clock.now() >= end:
            stats.note = f"остановлен в {end:%H:%M} по Москве — продолжим следующей ночью"
            return True
        return False

    for rp_id in profile_ids:
        if stop():
            return stats
        try:
            embed_profile(conn_factory, rp_id, http=http, now=clock.now())
            fails = 0
        except embed.ERRORS:
            fails += 1
            stats.errors += 1
    with conn_factory() as conn:
        todo = pending_people(conn, model)
        size = embed.batch_size(conn)
    for i in range(0, len(todo), size):
        if stop():
            return stats
        part = todo[i:i + size]
        stats.seen += len(part)
        try:
            _batch(conn_factory, part, model, http, clock.now())
            fails = 0
        except embed.ERRORS:
            fails += 1
            stats.errors += len(part)
            continue
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
