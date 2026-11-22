"""T13 semantic-search: пределы, векторы и флаг модели в /api/schema, vectors_pending в /api/search (S8–S12)."""
from datetime import timedelta

from test_reembed_night import bge  # noqa: F401 — фикстура службы bge-m3

from hr import db, embed, quota, search


def people(conn, n, prefix="Человек"):
    return [r["id"] for r in db.rows(conn, """INSERT INTO person (label) SELECT %s || ' ' || g
                                              FROM generate_series(1, %s) g RETURNING id""", (prefix, n))]


def put_vec(conn, pid, text, model):
    db.run(conn, """INSERT INTO person_embedding (person_id, embedding, embed_model) VALUES (%s, %s::vector, %s)
                    ON CONFLICT (person_id) DO UPDATE SET embedding = EXCLUDED.embedding,
                                                          embed_model = EXCLUDED.embed_model""",
           (pid, embed.literal(embed.hash_vector(text)), model))


def test_s11_s12_schema_quota_and_flag(conn, api, clock):
    today, yesterday = quota.day_of(clock.now()), quota.day_of(clock.now() - timedelta(days=1))
    db.run(conn, """INSERT INTO ops.daily_quota (day, key, used) VALUES
                    (%s, 'hh_full_resume', 214), (%s, 'llm_call', 12), (%s, 'hh_full_resume', 300)""",
           (today, today, yesterday))
    c, _, _ = api()
    r = c.get("/api/schema").json()
    assert r["quota"] == {"hh_full_resume": {"used": 214, "limit": 300}, "llm": {"used": 12, "limit": 500}}
    assert r["llm_assist_enabled"] is True
    db.run(conn, "UPDATE settings SET value = 'false' WHERE key = 'llm_assist_enabled'")
    db.run(conn, "UPDATE settings SET value = '250' WHERE key = 'hh_full_resume_per_day'")
    r = c.get("/api/schema").json()
    assert r["llm_assist_enabled"] is False and r["quota"]["hh_full_resume"]["limit"] == 250
    assert {"jobs", "counts"} <= set(r), "прежние поля на месте"


def test_s10_schema_vectors_pending_and_total(conn, api):
    before = db.val(conn, "SELECT count(*) FROM person")
    a, b, _ = people(conn, 3)
    put_vec(conn, a, "инженер", embed.method())
    put_vec(conn, b, "инженер", "other-1024")   # вектор другой модели — к пересчёту
    c, _, _ = api()
    v = c.get("/api/schema").json()["vectors"]
    assert v == {"model": embed.HASH, "pending": before + 2, "total": before + 3, "down_since": None}


def test_s08_schema_vectors_down_since(conn, api, bge):  # noqa: F811
    db.run(conn, """INSERT INTO ops.service_health (service, down_since, last_error)
                    VALUES ('embed', '2026-10-25 11:05+00', 'ConnectError')""")
    c, _, _ = api()
    v = c.get("/api/schema").json()["vectors"]
    assert v["model"] == "BAAI/bge-m3" and v["down_since"].startswith("2026-10-25T11:05")


def test_schema_without_base_all_forbidden(api):
    c, _, _ = api("hiring_manager", login="rnd_head")
    assert c.get("/api/schema").status_code == 403


def test_s10_search_vectors_pending_and_query_length(conn, api):
    before = db.val(conn, "SELECT count(*) FROM person")
    a, _ = people(conn, 2)
    put_vec(conn, a, "разработчик печатных плат", embed.method())
    c, _, _ = api()
    r = c.get("/api/search", params={"q": "разработчик печатных плат"})
    assert r.status_code == 200 and r.json()["vectors_pending"] == before + 1
    assert c.get("/api/search", params={"q": "п" * 300}).status_code == 200
    r = c.get("/api/search", params={"q": "п" * 301})
    assert r.status_code == 422 and r.json()["error"] == "validation"


def test_s09_mixed_models_full_result(conn):
    """При переходе на bge-m3 ближайшие в индексе — векторы прошлой модели; выдача всё равно полная."""
    q = "разработчик печатных плат"
    for pid in people(conn, 60, "Старый"):
        put_vec(conn, pid, q, "other-1024")
    for i, pid in enumerate(people(conn, 40, "Новый")):
        put_vec(conn, pid, f"инженер схемотехник {i}", embed.method())
    db.run(conn, "SET LOCAL enable_seqscan = off; SET LOCAL enable_sort = off")   # как на большой базе: по HNSW
    found = search.similar(conn, q, limit=30)
    assert len(found) == 30
