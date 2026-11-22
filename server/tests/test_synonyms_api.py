"""T6: API синонимов (S1–S4): чтение со скоупом (AD-7), правка и откат по праву profile.synonyms."""
import json
from types import SimpleNamespace

import httpx
import pytest
from factories import org, user

from hr import auth, config, db, gigachat, quota, synonyms
from hr.deps import get_llm_http
from hr.main import app


class FakeLLM:
    def __init__(self, status=200):
        self.status, self.calls = status, 0

    def __call__(self, req):
        self.calls += 1
        if self.status != 200:
            return httpx.Response(self.status)
        reply = {"synonyms": {"Altium": ["P-CAD", "KiCad"], "схемотехника": ["разработка схем"]}}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(reply, ensure_ascii=False)}}]})


@pytest.fixture
def model(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.test/v1")
    monkeypatch.setenv("LLM_MODEL", "m")
    monkeypatch.setenv("GIGACHAT_AUTH_KEY", "")
    config.get_config.cache_clear()
    gigachat.reset()
    fake = FakeLLM()
    app.dependency_overrides[get_llm_http] = lambda: httpx.Client(transport=httpx.MockTransport(fake))
    yield fake
    app.dependency_overrides.pop(get_llm_http, None)
    config.get_config.cache_clear()


@pytest.fixture
def rescored(monkeypatch):
    calls = []
    monkeypatch.setattr(synonyms, "rescore", lambda cf, pid: calls.append(pid))
    return calls


@pytest.fixture
def setup(conn, clock, api):
    o = org(conn, clock.now())
    rec, rh, rid = api("recruiter")
    mgr, mh, _ = api("hiring_manager", login="rnd_head")
    user(conn, "rnd_head", "hiring_manager", o["units"]["u-rnd"])
    return o, (rec, rh, rid), (mgr, mh)


def url(pid, tail=""):
    return f"/api/profiles/{pid}/synonyms{tail}"


def test_s01_suggest_returns_terms_and_saves_nothing(conn, setup, model):
    o, (rec, h, _), _ = setup
    r = rec.post(url(o["eng"]["id"], "/suggest"), headers=h)
    assert r.status_code == 200
    assert r.json() == {"suggestions": {"Altium": ["P-CAD", "KiCad"], "схемотехника": ["разработка схем"]}}
    assert db.val(conn, "SELECT count(*) FROM role_synonym") == 0, "без кнопки «Сохранить» ничего не пишется"


def test_s02_put_saves_with_journal_and_background_rescore(conn, setup, rescored):
    o, (rec, h, rid), _ = setup
    pid = o["eng"]["id"]
    r = rec.put(url(pid), json={"items": [{"keyword": "Altium", "synonyms": ["P-CAD", "KiCad"]}],
                                "base_change_id": None}, headers=h)
    assert r.status_code == 200
    cid = r.json()["change_id"]
    assert rescored == [pid], "профиль пересчитывается в фоне"
    got = rec.get(url(pid)).json()
    assert got["role_code"] == "engineer_hw_lead" and got["can_edit"] is True
    assert {"keyword": "Altium", "synonyms": ["P-CAD", "KiCad"]} in got["items"]
    assert {"keyword": "ЭМС", "synonyms": []} in got["items"]
    [hist] = got["history"]
    assert hist["id"] == cid and hist["by"] == "Проверочный recruiter" and hist["rollback_of"] is None
    assert hist["diff"] == {"altium": {"+": ["P-CAD", "KiCad"], "-": []}} and hist["can_rollback"] is True
    assert hist["applied_at"]
    assert db.val(conn, "SELECT applied_by FROM agent_change WHERE id = %s", (cid,)) == rid


def test_s02_put_errors_validation_and_stale(conn, setup, rescored):
    o, (rec, h, _), _ = setup
    pid = o["eng"]["id"]
    bad = rec.put(url(pid), json={"items": [{"keyword": "Altium", "synonyms": ["<b>"]}], "base_change_id": None},
                  headers=h)
    assert bad.status_code == 422 and bad.json()["error"] == "validation"
    first = rec.put(url(pid), json={"items": [{"keyword": "Altium", "synonyms": ["P-CAD"]}],
                                    "base_change_id": None}, headers=h).json()["change_id"]
    same = rec.put(url(pid), json={"items": [{"keyword": "Altium", "synonyms": ["P-CAD"]}],
                                   "base_change_id": first}, headers=h)
    assert same.status_code == 422 and "нет изменений" in same.json()["message"]
    stale = rec.put(url(pid), json={"items": [{"keyword": "Altium", "synonyms": ["KiCad"]}],
                                    "base_change_id": None}, headers=h)
    assert stale.status_code == 409 and stale.json()["error"] == "stale"
    wrong = rec.put(url(pid), json={"items": [], "base_change_id": "x"}, headers=h)
    assert wrong.status_code == 422 and wrong.json()["error"] == "validation"
    assert rescored == [pid]


def test_s03_rollback_latest_ok_older_409(conn, setup, rescored):
    o, (rec, h, _), _ = setup
    pid = o["eng"]["id"]
    first = rec.put(url(pid), json={"items": [{"keyword": "Altium", "synonyms": ["P-CAD"]}],
                                    "base_change_id": None}, headers=h).json()["change_id"]
    second = rec.put(url(pid), json={"items": [{"keyword": "Altium", "synonyms": ["KiCad"]}],
                                     "base_change_id": first}, headers=h).json()["change_id"]
    old = rec.post(url(pid, f"/rollback/{first}"), headers=h)
    assert old.status_code == 409 and old.json()["error"] == "not_latest"
    assert rec.post(url(pid, "/rollback/999999"), headers=h).status_code == 404
    r = rec.post(url(pid, f"/rollback/{second}"), headers=h)
    assert r.status_code == 200
    back = r.json()["change_id"]
    got = rec.get(url(pid)).json()
    assert {"keyword": "Altium", "synonyms": ["P-CAD"]} in got["items"], "синонимы — как до правки"
    assert [x["id"] for x in got["history"]] == [back, second, first]
    assert got["history"][0]["rollback_of"] == second and got["history"][0]["can_rollback"] is True
    assert [x["can_rollback"] for x in got["history"][1:]] == [False, False]
    assert rescored == [pid, pid, pid]


def test_s04_suggest_errors_have_scenario_texts(conn, setup, model, monkeypatch):
    o, (rec, h, _), _ = setup
    model.status = 500
    r = rec.post(url(o["eng"]["id"], "/suggest"), headers=h)
    assert r.status_code == 503 and r.json() == {
        "error": "llm_unavailable", "message": "Модель сейчас недоступна — впишите синонимы сами"}
    monkeypatch.setattr(quota, "take", lambda *a, **k: False)
    r = rec.post(url(o["eng"]["id"], "/suggest"), headers=h)
    assert r.status_code == 429 and r.json() == {
        "error": "llm_daily_limit", "message": "Предел запросов к модели на сегодня исчерпан"}
    db.run(conn, "UPDATE role_profile SET keywords = '{}' WHERE id = %s", (o["eng"]["id"],))
    r = rec.post(url(o["eng"]["id"], "/suggest"), headers=h)
    # синонимы — у утверждённой версии: «сохраните» вводило в заблуждение (ревью, находка 10)
    assert r.status_code == 422 and r.json() == {
        "error": "no_keywords", "message": "Сначала добавьте ключевые слова и утвердите версию профиля"}
    assert rec.put(url(o["eng"]["id"]), json={"items": [], "base_change_id": None}, headers=h).status_code == 422, \
        "вписать вручную можно и без модели — ошибка только от проверки"


def test_manager_reads_own_scope_and_cannot_write(conn, setup, model, rescored):
    o, _, (mgr, h) = setup
    pid = o["eng"]["id"]
    got = mgr.get(url(pid))
    assert got.status_code == 200 and got.json()["can_edit"] is False
    assert mgr.get(url(o["sales"]["id"])).status_code == 404, "вне скоупа — как будто профиля нет"
    for r in (mgr.post(url(pid, "/suggest"), headers=h),
              mgr.put(url(pid), json={"items": [{"keyword": "Altium", "synonyms": ["P-CAD"]}],
                                      "base_change_id": None}, headers=h),
              mgr.post(url(pid, "/rollback/1"), headers=h)):
        assert r.status_code == 403
    assert model.calls == 0 and rescored == []
    assert db.val(conn, "SELECT count(*) FROM role_synonym") == 0


def test_not_found_and_routes_scoped(setup, rescored):
    _, (rec, h, _), _ = setup
    assert rec.get(url(999999)).status_code == 404
    assert rec.put(url(999999), json={"items": [], "base_change_id": None}, headers=h).status_code == 404
    assert rec.post(url(999999, "/rollback/1"), headers=h).status_code == 404
    flat = SimpleNamespace(routes=list(api_routes(app.routes)))
    assert {f"{m} {r.path}" for r in flat.routes if "synonyms" in r.path for m in r.methods} == {
        "GET /api/profiles/{profile_id}/synonyms", "POST /api/profiles/{profile_id}/synonyms/suggest",
        "PUT /api/profiles/{profile_id}/synonyms", "POST /api/profiles/{profile_id}/synonyms/rollback/{change_id}"}
    assert auth.routes_unscoped(flat) == [] and auth.routes_unscoped(flat, "profile.synonyms") == []


def api_routes(routes):
    """FastAPI 0.16x оборачивает include_router в _IncludedRouter: app.routes не видит маршрутов —
    разворачиваем, чтобы проверка скоупа видела новые маршруты на самом деле."""
    for r in routes:
        if hasattr(r, "original_router"):
            yield from api_routes(r.original_router.routes)
        elif hasattr(r, "dependant"):
            yield r
