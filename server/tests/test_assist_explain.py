"""T8 workplace: разбор моделью по требованию с кэшем, проверкой ответа и шаблоном (S7, S8; W1, W4)."""
import json

import httpx
import pytest
from factories import hh_item, profile, source

from hr import config, db, gigachat
from hr.normalize import normalize
from hr.pipeline import process

GOOD = {"why": "Сильный опыт в схемотехнике и трассировке.", "gap": "Не видно опыта с ЭМС-испытаниями.",
        "questions": ["Какие платы вы разводили последними?"]}


@pytest.fixture
def llm_env(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.test/v1")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("GIGACHAT_AUTH_KEY", "")
    config.get_config.cache_clear()
    gigachat.reset()
    yield
    config.get_config.cache_clear()


class FakeLLM:
    def __init__(self, reply=None, status=200):
        self.reply, self.status, self.requests = reply if reply is not None else GOOD, status, []

    def __call__(self, req):
        self.requests.append(json.loads(req.content))
        if self.status != 200:
            return httpx.Response(self.status)
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(self.reply,
                                                                                         ensure_ascii=False)}}]})

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self))


@pytest.fixture
def pair(conn, clock):
    prof = profile(conn)
    it = hh_item(last_name="Секретов", first_name="Иван")
    pid = process(conn, source(conn, "hh"), normalize("hh", it), it, clock.now()).person_id
    return pid, prof["id"]


def explain(api, pid, rp, fake):
    from hr.deps import get_llm_http
    from hr.main import app
    app.dependency_overrides[get_llm_http] = fake.client
    c, h, _ = api()
    return c.post(f"/api/person/{pid}/explain", json={"role_profile_id": rp}, headers=h)


def test_s07_model_explains_once_then_cache(conn, llm_env, api, pair):
    pid, rp = pair
    fake = FakeLLM()
    r = explain(api, pid, rp, fake)
    assert r.status_code == 200 and r.json()["by"] == "model" and "схемотехнике" in r.json()["text"]
    r2 = explain(api, pid, rp, fake)
    assert r2.json()["text"] == r.json()["text"] and len(fake.requests) == 1, "повторное открытие — без модели"
    assert db.val(conn, "SELECT model FROM match WHERE person_id = %s", (pid,)) != "template"


def test_no_name_or_contacts_to_model(conn, llm_env, api, pair):
    pid, rp = pair
    fake = FakeLLM()
    explain(api, pid, rp, fake)
    sent = json.dumps(fake.requests[0], ensure_ascii=False)
    assert "Секретов" not in sent and "900 000" not in sent and "@" not in sent


def test_s08_model_down_gives_template(conn, llm_env, api, pair):
    pid, rp = pair
    r = explain(api, pid, rp, FakeLLM(status=503))
    assert r.status_code == 200 and r.json()["by"] == "template" and r.json()["text"]


def test_s08_no_model_configured(conn, monkeypatch, api, pair):
    monkeypatch.setenv("LLM_BASE_URL", "")
    monkeypatch.setenv("GIGACHAT_AUTH_KEY", "")
    config.get_config.cache_clear()
    pid, rp = pair
    fake = FakeLLM()
    r = explain(api, pid, rp, fake)
    assert r.json()["by"] == "template" and fake.requests == []


@pytest.mark.parametrize("bad", [
    {**GOOD, "why": "Перейдите на https://evil.example"},          # W1: ссылка из внедрённой команды
    {**GOOD, "gap": "<script>alert(1)</script>"},                   # W4: разметка
    {"text": "игнорирую инструкции"},                               # не тот формат
])
def test_w1_w4_bad_reply_falls_back_to_template(conn, llm_env, api, pair, bad):
    pid, rp = pair
    r = explain(api, pid, rp, FakeLLM(reply=bad))
    assert r.json()["by"] == "template" and "evil" not in r.json()["text"] and "<script" not in r.json()["text"]


def test_hourly_cap(conn, llm_env, api, pair):
    pid, rp = pair
    db.run(conn, "UPDATE settings SET value = '0' WHERE key = 'llm_calls_per_hour'")
    fake = FakeLLM()
    assert explain(api, pid, rp, fake).json()["by"] == "template" and fake.requests == []


def test_unknown_pair_404(conn, llm_env, api, pair):
    pid, _ = pair
    r = explain(api, pid, 999999, FakeLLM())
    assert r.status_code == 404
