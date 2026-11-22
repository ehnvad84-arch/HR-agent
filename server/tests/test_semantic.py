"""T11 semantic-search: признак «Близость по смыслу» и вектор «серой зоны» при входе в базу (S7, S8)."""
import json
import math

import httpx
import pytest
from cryptography.fernet import Fernet
from factories import ENGINEER_WEIGHTS, hh_item, profile, source
from test_collect_hh import FakeHH

from hr import collect, config, db, embed, hh, pipeline, scoring, search, tasks
from hr.http import RateLimiter
from hr.normalize import normalize

COS = 0.82
E1 = [1.0] + [0.0] * 1023
CLOSE = [COS, math.sqrt(1 - COS * COS)] + [0.0] * 1022
FAR = [0.0, 0.0, 1.0] + [0.0] * 1021


class FakeBGE:
    """Заглушка bge-m3: профиль — E1, резюме — под углом cos 0,82 к нему, «Продавец» — перпендикулярно."""

    def __init__(self):
        self.status, self.texts = 200, []

    def __call__(self, req):
        texts = json.loads(req.content)["input"]
        self.texts += texts
        if self.status != 200:
            return httpx.Response(self.status)
        vec = lambda t: E1 if t.startswith("Ведущий инженер-электронщик.") else FAR if "Продавец" in t else CLOSE  # noqa: E731
        return httpx.Response(200, json={"data": [{"index": i, "embedding": vec(t)} for i, t in enumerate(texts)]})

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self))

    def resume_calls(self):
        return [t for t in self.texts if not t.startswith("Ведущий инженер-электронщик.")]


@pytest.fixture(autouse=True)
def hh_env(monkeypatch, conn, clock):
    monkeypatch.setenv("SECRETS_KEY", Fernet.generate_key().decode())
    config.get_config.cache_clear()
    hh.store_tokens(conn, {"access_token": "AT", "refresh_token": "RT", "expires_in": 30 * 86400}, "777",
                    clock.now())
    yield
    config.get_config.cache_clear()


@pytest.fixture
def bge(monkeypatch):
    monkeypatch.setenv("EMBED_BASE_URL", "http://emb.test/v1")
    monkeypatch.setenv("EMBED_MODEL", "BAAI/bge-m3")
    config.get_config.cache_clear()
    fake = FakeBGE()
    monkeypatch.setattr(embed, "client", fake.client)
    yield fake
    config.get_config.cache_clear()


def other_words(rid="g1"):
    """Разработчик электроники другими словами: ни одного ключа профиля, без смысла — 55 из 60."""
    exp = [{"company": f"Завод {rid}", "position": "Разработчик электроники", "start": "2016-01-01", "end": None}]
    return hh_item(id=rid, title="Разработчик электроники", skill_set=["проектирование печатных плат", "P-CAD"],
                   experience=exp, total_experience={"months": 120})


def engineer():
    return scoring.Profile(title="Ведущий инженер-электронщик",
                           keywords=["Altium", "схемотехника", "трассировка", "ЭМС"],
                           donor_companies=["Болид"], weights=dict(ENGINEER_WEIGHTS), salary_min=150000,
                           salary_max=200000, dept_code="rnd_hw")


def person_of(item):
    return pipeline.norm_person(normalize("hh", item))


def crawl(conn_factory, fake, clock):
    return collect.collect_hh(conn_factory, http=fake.client(), clock=clock, limiter=RateLimiter(clock))


def match_of(conn, external_id):
    return db.one(conn, """SELECT m.* FROM match m JOIN observation o ON o.person_id = m.person_id
                           WHERE o.external_id = %s""", (external_id,))


# ------------------------------------------------------------------ чистая функция балла

def test_s07_semantic_feature_label_and_weight():
    p, prm = person_of(other_words()), scoring.Params()
    s = scoring.score(p, engineer(), prm, semantic=COS)
    f = s.features["semantic"]
    assert f["label"] == "Близость по смыслу" and f["detail"] == "близко к профилю по смыслу: 82 %"
    assert f["max"] == round(15 * 100 / 115) and f["points"] == f["max"] and f["sim"] == 0.82
    assert s.score == 61 > scoring.score(p, engineer(), prm).score == 55
    low = scoring.score(p, engineer(), prm, semantic=0.30).features["semantic"]
    assert low["points"] == 0 and low["max"] == f["max"], "ниже semantic_sim_floor — ноль, но признак в сумме"
    half = scoring.score(p, engineer(), prm, semantic=0.575).features["semantic"]
    assert half["points"] == round(f["max"] / 2)


def test_s08_without_vector_score_is_unchanged():
    p, prm = person_of(other_words()), scoring.Params()
    before = scoring.score(p, engineer(), prm)
    for skip, text in (("no_vector", "вектор резюме ещё не посчитан"), ("embed_down", "модель векторов недоступна")):
        s = scoring.score(p, engineer(), prm, semantic_skip=skip)
        assert s.score == before.score
        assert {k: v for k, v in s.features.items() if k != "semantic"} == before.features
        assert s.features["semantic"] == {"label": "Близость по смыслу", "points": 0, "max": 0, "skipped": skip,
                                          "detail": text}
        assert "близость" not in scoring.template_explanation(s)["why"].lower()


def test_s07_weight_from_profile_and_zero_turns_off():
    p, prm = person_of(other_words()), scoring.Params()
    pr = engineer()
    pr.weights["semantic"] = 0
    assert "semantic" not in scoring.score(p, pr, prm, semantic=COS).features
    assert "semantic" not in scoring.score(p, pr, prm, semantic_skip="no_vector").features
    pr.weights["semantic"] = 30
    assert scoring.score(p, pr, prm, semantic=COS).features["semantic"]["max"] == round(30 * 100 / 130)
    prm.semantic_weight = 5
    assert scoring.score(p, engineer(), prm, semantic=COS).features["semantic"]["max"] == round(5 * 100 / 105)
    assert "semantic" in scoring.FEATURE_LABELS, "вес «semantic» принимает проверка профиля"


# ------------------------------------------------------------------ вход в базу через hh

def test_s07_close_resume_without_keywords_enters_base(conn, conn_factory, clock, bge):
    pr = profile(conn)
    assert search.embed_profile(conn_factory, pr["id"], now=clock.now())
    fake = FakeHH(items=[other_words("g1"), other_words("g2"), hh_item(id="strong")])
    st = crawl(conn_factory, fake, clock)
    assert st.persons == 3
    m = match_of(conn, "g1")
    assert m["score"] >= 60 > scoring.score(person_of(other_words()), engineer(), scoring.Params()).score
    assert m["features"]["semantic"]["detail"] == "близко к профилю по смыслу: 82 %"
    assert db.val(conn, """SELECT embed_model FROM person_embedding e JOIN observation o ON o.person_id = e.person_id
                           WHERE o.external_id = 'g1'""") == "BAAI/bge-m3"
    assert len(bge.resume_calls()) <= len(fake.full_fetches), "вызовов bge-m3 при входе — не больше полных резюме"


def test_s07_rescore_profile_and_alerts_keep_semantic(conn, conn_factory, clock, bge):
    pr = profile(conn)
    search.embed_profile(conn_factory, pr["id"], now=clock.now())
    crawl(conn_factory, FakeHH(items=[other_words("g1")]), clock)
    pipeline.rescore_profile(conn_factory, pr["id"], now=clock.now())
    assert match_of(conn, "g1")["features"]["semantic"]["sim"] == 0.82
    pid = db.val(conn, "SELECT person_id FROM observation WHERE external_id = 'g1'")
    db.run(conn, "UPDATE obs_event SET processed = false WHERE person_id = %s", (pid,))
    tasks.alerts_pass(conn_factory, clock=clock)
    m = match_of(conn, "g1")
    assert m["features"]["semantic"]["points"] > 0 and m["score"] >= 60


def test_s08_embed_down_collect_goes_on_without_meaning(conn, conn_factory, clock, bge):
    pr = profile(conn)
    search.embed_profile(conn_factory, pr["id"], now=clock.now())
    bge.status = 500
    fake = FakeHH(items=[other_words("g1"), other_words("g2"), hh_item(id="strong")])
    st = crawl(conn_factory, fake, clock)
    assert st.errors == 0 and st.persons == 1, "сбор не падает, похожий только по смыслу не входит"
    assert len(bge.resume_calls()) == 1, "после отказа служба в этом прогоне не вызывается"
    assert embed.down_since(conn) is not None
    f = match_of(conn, "strong")["features"]["semantic"]
    assert f["skipped"] == "embed_down" and f["max"] == 0


def test_s08_person_without_vector_has_no_vector_mark(conn, conn_factory, clock, bge):
    pr = profile(conn)
    it = hh_item(id="strong")
    with conn_factory() as c:
        r = pipeline.process(c, source(c, "hh"), normalize("hh", it), it, clock.now())
    s = next(x for x in r.scores if x["profile"]["id"] == pr["id"])
    assert s["s"].features["semantic"]["skipped"] == "no_vector"
    assert s["score"] == scoring.score(person_of(it), engineer(), scoring.Params()).score


def test_s08_hash_method_score_as_before(conn, conn_factory, clock, monkeypatch):
    monkeypatch.setenv("EMBED_BASE_URL", "")
    config.get_config.cache_clear()
    pr = profile(conn)
    search.embed_profile(conn_factory, pr["id"], now=clock.now())
    st = crawl(conn_factory, FakeHH(items=[other_words("g1"), hh_item(id="strong")]), clock)
    assert st.persons == 1, "при hash-v1 смысла нет: резюме другими словами не входит"
    m = match_of(conn, "strong")
    assert "semantic" not in m["features"]
    assert m["score"] == scoring.score(person_of(hh_item(id="strong")), engineer(), scoring.Params()).score
