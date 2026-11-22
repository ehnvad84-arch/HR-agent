"""T10: векторы пачками, EMBED_TIMEOUT_S, ops.service_health('embed'), вектор профиля (S8, S9)."""
import json
from datetime import timedelta

import httpx
import pytest
from factories import profile

from hr import config, db, embed, search


class FakeEmbed:
    """Заглушка bge-m3: /v1/embeddings с пачкой input: [...]."""

    def __init__(self, status=200):
        self.status, self.bodies = status, []

    def __call__(self, req):
        body = json.loads(req.content)
        self.bodies.append(body)
        if self.status != 200:
            return httpx.Response(self.status)
        texts = body["input"] if isinstance(body["input"], list) else [body["input"]]
        return httpx.Response(200, json={"data": [{"index": i, "embedding": [0.03125] * 1024}
                                                  for i in range(len(texts))]})

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self))


@pytest.fixture
def bge(monkeypatch):
    monkeypatch.setenv("EMBED_BASE_URL", "http://emb.test/v1")
    monkeypatch.setenv("EMBED_MODEL", "BAAI/bge-m3")
    config.get_config.cache_clear()
    fake = FakeEmbed()
    monkeypatch.setattr(embed, "client", fake.client)
    yield fake
    config.get_config.cache_clear()


def health(conn):
    return db.one(conn, "SELECT * FROM ops.service_health WHERE service = 'embed'")


def test_s08_batch_of_32_is_one_request(bge):
    out = embed.vectors([f"резюме {i}" for i in range(32)], batch_size=32)
    assert len(out) == 32 and len(bge.bodies) == 1 and len(bge.bodies[0]["input"]) == 32
    embed.vectors([f"резюме {i}" for i in range(33)], batch_size=32)
    assert [len(b["input"]) for b in bge.bodies[1:]] == [32, 1]


def test_s08_tracked_takes_batch_size_from_settings(conn, conn_factory, bge, clock):
    db.run(conn, "UPDATE settings SET value = '5' WHERE key = 'embed_batch_size'")
    assert len(embed.tracked(conn_factory, ["а"] * 12, now=clock.now())) == 12
    assert [len(b["input"]) for b in bge.bodies] == [5, 5, 2]


def test_s08_failure_sets_down_since_success_clears(conn, conn_factory, bge, clock):
    bge.status = 500
    t0 = clock.now()
    with pytest.raises(embed.ERRORS):
        embed.tracked(conn_factory, ["текст"], now=t0)
    h = health(conn)
    assert h["down_since"] == t0 and h["last_error"] == "HTTPStatusError"
    with pytest.raises(embed.ERRORS):
        embed.tracked(conn_factory, ["текст"], now=t0 + timedelta(minutes=5))
    assert health(conn)["down_since"] == t0          # первый отказ, а не последний
    assert embed.down_since(conn) == t0
    bge.status = 200
    embed.tracked(conn_factory, ["текст"], now=t0 + timedelta(minutes=10))
    h = health(conn)
    assert h["down_since"] is None and h["last_ok_at"] == t0 + timedelta(minutes=10)
    assert embed.down_since(conn) is None


def test_s08_hash_method_needs_no_service(conn, conn_factory, monkeypatch, clock):
    monkeypatch.setenv("EMBED_BASE_URL", "")
    config.get_config.cache_clear()
    try:
        out = embed.tracked(conn_factory, ["инженер Altium"], now=clock.now())
        assert out == [embed.hash_vector("инженер Altium")]
        assert health(conn) is None
    finally:
        config.get_config.cache_clear()


def test_s08_timeout_from_env(monkeypatch):
    monkeypatch.delenv("EMBED_TIMEOUT_S", raising=False)
    config.get_config.cache_clear()
    assert config.get_config().embed_timeout_s == 60
    monkeypatch.setenv("EMBED_TIMEOUT_S", "7")
    config.get_config.cache_clear()
    try:
        assert embed.client().timeout.read == 7
    finally:
        config.get_config.cache_clear()


def test_s09_contacts_not_sent_to_model(bge):
    embed.vectors(["Инженер, +7 (918) 123-45-67, ivan@example.ru, Altium"])
    sent = json.dumps(bge.bodies, ensure_ascii=False)
    assert "123-45-67" not in sent and "ivan@example.ru" not in sent and "Altium" in sent


def test_s09_profile_vector_only_on_text_change(conn, conn_factory, bge, clock):
    pr = profile(conn, nice_to_have=["опыт ЭМС-испытаний"])
    assert search.embed_profile(conn_factory, pr["id"], now=clock.now()) is True
    row = db.one(conn, "SELECT * FROM ops.profile_embedding WHERE role_profile_id = %s", (pr["id"],))
    assert row["embed_model"] == "BAAI/bge-m3" and len(bge.bodies) == 1
    text = bge.bodies[0]["input"][0]
    assert "Ведущий инженер-электронщик" in text and "Altium" in text and "ЭМС-испытаний" in text
    assert search.embed_profile(conn_factory, pr["id"], now=clock.now()) is False
    assert len(bge.bodies) == 1                       # текст тот же — модель не вызывается
    db.run(conn, """INSERT INTO role_synonym (role_code, keyword, synonyms) VALUES (%s, 'altium', %s)""",
           (pr["code"], ["P-CAD"]))
    assert search.embed_profile(conn_factory, pr["id"], now=clock.now()) is True
    assert "P-CAD" in bge.bodies[1]["input"][0]
    after = db.one(conn, "SELECT * FROM ops.profile_embedding WHERE role_profile_id = %s", (pr["id"],))
    assert after["text_sha256"] != row["text_sha256"]


def test_s09_profile_vector_recomputed_on_model_change(conn, conn_factory, bge, clock, monkeypatch):
    pr = profile(conn)
    search.embed_profile(conn_factory, pr["id"], now=clock.now())
    monkeypatch.setenv("EMBED_MODEL", "other-1024")
    config.get_config.cache_clear()
    assert search.embed_profile(conn_factory, pr["id"], now=clock.now()) is True
    assert db.val(conn, "SELECT embed_model FROM ops.profile_embedding WHERE role_profile_id = %s",
                  (pr["id"],)) == "other-1024"


def test_s09_profile_vector_failure_keeps_old_and_marks_down(conn, conn_factory, bge, clock):
    pr = profile(conn)
    search.embed_profile(conn_factory, pr["id"], now=clock.now())
    db.run(conn, "INSERT INTO role_synonym (role_code, keyword, synonyms) VALUES (%s, 'altium', %s)",
           (pr["code"], ["P-CAD"]))
    bge.status = 503
    with pytest.raises(embed.ERRORS):
        search.embed_profile(conn_factory, pr["id"], now=clock.now())
    assert db.val(conn, "SELECT count(*) FROM ops.profile_embedding") == 1
    assert health(conn)["down_since"] is not None


def test_s08_search_503_when_model_down(conn, api, bge):
    bge.status = 500
    c, _, _ = api()
    r = c.get("/api/search", params={"q": "разработчик печатных плат"})
    assert r.status_code == 503 and r.json()["error"] == "embed"
    assert health(conn)["down_since"] is not None
