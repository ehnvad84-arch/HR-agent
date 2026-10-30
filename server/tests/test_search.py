"""T19: поиск по смыслу (S31)."""
import pytest
from factories import hh_item, profile, source

from hr import db, embed, search
from hr.normalize import normalize
from hr.pipeline import process

PEOPLE = [
    ("eng", "Ведущий инженер-электронщик", ["Altium Designer", "схемотехника", "трассировка плат"], "Приборный завод"),
    ("otk", "Инженер ОТК", ["AQL", "входной контроль", "ISO 9001"], "Завод электроники"),
    ("sales", "Менеджер проектных продаж", ["44-ФЗ", "CRM", "переговоры"], "Дистрибьютор СБ"),
]


@pytest.fixture
def people(conn, clock):
    profile(conn, weights={"geo": 100})   # все трое из Краснодара — порог проходят все
    ids = {}
    for key, title, skills, company in PEOPLE:
        it = hh_item(id=key, title=title, skill_set=skills, area={"id": "53", "name": "Краснодар"},
                     experience=[{"company": company, "position": title, "start": "2019-01-01", "end": None}])
        process(conn, source(conn, "hh"), normalize("hh", it), it, clock.now())
        ids[key] = db.val(conn, "SELECT person_id FROM observation WHERE external_id = %s", (key,))
    db.run(conn, "UPDATE person SET watch = 'observed'")
    assert all(ids.values())
    return ids


def test_s31_vectors_written_with_method(conn, people):
    rows = db.rows(conn, "SELECT person_id, embed_model FROM person_embedding")
    assert {r["person_id"] for r in rows} == set(people.values())
    assert {r["embed_model"] for r in rows} == {"hash-v1"}


def test_s31_similar_finds_engineer_first(conn, people):
    res = search.similar(conn, "схемотехник Altium")
    assert res[0]["person_id"] == people["eng"]
    assert res[0]["similarity"] > res[-1]["similarity"]
    assert {"person_id", "label", "city", "similarity"} <= set(res[0])


def test_s31_quality_check(conn, people):
    assert search.similar(conn, "входной контроль качества AQL")[0]["person_id"] == people["otk"]
    assert search.similar(conn, "продажи по 44-ФЗ")[0]["person_id"] == people["sales"]


def test_s31_other_method_vectors_excluded(conn, people):
    db.run(conn, "UPDATE person_embedding SET embed_model = 'other-model' WHERE person_id = %s", (people["eng"],))
    ids = [r["person_id"] for r in search.similar(conn, "схемотехник Altium")]
    assert people["eng"] not in ids


def test_empty_query_rejected(conn):
    with pytest.raises(ValueError):
        search.similar(conn, "   ")


def test_hash_vector_is_normalized_and_1024():
    v = embed.hash_vector("инженер Altium")
    assert len(v) == 1024 and abs(sum(x * x for x in v) - 1) < 1e-6


def test_reembed_switches_method_and_fills_missing(conn, conn_factory, people, monkeypatch, clock):
    import httpx

    from hr import config
    monkeypatch.setenv("EMBED_BASE_URL", "http://emb.test/v1")
    monkeypatch.setenv("EMBED_MODEL", "emb-1024")
    config.get_config.cache_clear()
    try:
        calls = []

        def handler(req):
            calls.append(req)
            return httpx.Response(200, json={"data": [{"embedding": [0.03125] * 1024}]})

        stats = search.reembed(conn_factory, http=httpx.Client(transport=httpx.MockTransport(handler)), clock=clock)
        assert stats.changed == 3 and len(calls) == 3
        assert {r["embed_model"] for r in db.rows(conn, "SELECT embed_model FROM person_embedding")} == {"emb-1024"}
        again = search.reembed(conn_factory, http=httpx.Client(transport=httpx.MockTransport(handler)),
                               only_stale=True, clock=clock)
        assert again.changed == 0
    finally:
        config.get_config.cache_clear()
