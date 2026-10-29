"""T18 workplace: недельная сводка — группировка, решение, ночное применение (S30, S31; 19, 20; W2)."""
import json

import httpx
import pytest

from hr import config, db, gigachat, learning, tasks

SIMILAR = ["Письма для ОТК звучат как для продажников, людям на производстве такой тон не нравится совсем.",
           "Тон писем для ОТК как у продажников, производственным людям это не нравится, два отказа подряд.",
           "Для ОТК письма звучат по-продажному, на производстве такой тон не любят, меняйте шаблон писем."]
OTHER = "В выдаче по инженерам слишком много людей из Москвы без готовности к переезду в Краснодар."


class FakeLLM:
    def __init__(self, target):
        self.target, self.requests = target, []

    def __call__(self, req):
        self.requests.append(json.loads(req.content))
        out = {"title": "Отдельный тон первого письма для ОТК", "rationale": "Три похожих замечания за неделю.",
               "question": "Завести отдельный шаблон?", "target": self.target}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(out, ensure_ascii=False)}}]})

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self))


@pytest.fixture
def no_model(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "")
    monkeypatch.setenv("GIGACHAT_AUTH_KEY", "")
    config.get_config.cache_clear()
    yield
    config.get_config.cache_clear()


@pytest.fixture
def with_model(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.test/v1")
    monkeypatch.setenv("LLM_MODEL", "m")
    monkeypatch.setenv("GIGACHAT_AUTH_KEY", "")
    config.get_config.cache_clear()
    gigachat.reset()
    yield
    config.get_config.cache_clear()


def write(conn_factory, uid, clock, texts):
    for t in texts:
        learning.post(conn_factory, uid, t, "criticism", clock.now())


def boss(conn):
    return db.val(conn, """INSERT INTO app_user (login, full_name, role)
                           VALUES ('b', 'Б', 'head_recruiter') RETURNING id""")


def test_s30_three_similar_give_one_proposal(conn, conn_factory, clock, no_model):
    uid = boss(conn)
    write(conn_factory, uid, clock, SIMILAR + [OTHER])
    st = learning.build_digest(conn_factory, clock.now())
    props = db.rows(conn, "SELECT title, rationale, target FROM digest_proposal ORDER BY id")
    assert st.new == 2 and len(props) == 2
    assert any("3 сообщ" in p["rationale"] for p in props), "одно предложение с обоснованием на трёх"
    assert db.val(conn, "SELECT count(*) FROM feedback_message WHERE digest_id IS NULL") == 0
    assert "digest" in tasks.TASKS and "apply_digest" in tasks.TASKS


def test_s31_accept_applied_at_night_with_author(conn, conn_factory, clock, api, with_model):
    from hr.deps import get_llm_http
    from hr.main import app
    c, h, uid = api()
    write(conn_factory, uid, clock, SIMILAR)
    fake = FakeLLM({"setting": "watch_threshold", "value": 65})
    learning.build_digest(conn_factory, clock.now(), http=fake.client())
    app.dependency_overrides[get_llm_http] = fake.client
    cur = c.get("/api/digest/current").json()
    pid = cur["proposals"][0]["id"]
    assert cur["proposals"][0]["target"] == {"setting": "watch_threshold", "value": 65}
    r = c.post(f"/api/digest/{pid}/decide", json={"decision": "accepted", "note": "согласна"}, headers=h)
    assert r.status_code == 200
    assert db.setting(conn, "watch_threshold") == 60, "до ночи не меняется"
    clock.advance(seconds=8 * 3600)
    learning.apply_digest(conn_factory, now=clock.now())
    assert db.setting(conn, "watch_threshold") == 65
    ch = db.one(conn, """SELECT kind, applied_by, before, after, proposal_id FROM agent_change
                         ORDER BY id DESC LIMIT 1""")
    assert ch["kind"] == "threshold" and ch["applied_by"] == uid and ch["proposal_id"] == pid
    assert ch["before"]["value"] == 60 and ch["after"]["value"] == 65
    assert db.val(conn, "SELECT applied_at FROM digest_proposal WHERE id = %s", (pid,))
    assert c.post(f"/api/digest/{pid}/decide", json={"decision": "declined"}, headers=h).status_code == 409


def test_w2_out_of_bounds_target_dropped(conn, conn_factory, clock, with_model):
    uid = boss(conn)
    write(conn_factory, uid, clock, SIMILAR)
    learning.build_digest(conn_factory, clock.now(), http=FakeLLM({"setting": "watch_threshold", "value": 0}).client())
    p = db.one(conn, "SELECT id, target FROM digest_proposal")
    assert p["target"] == {}, "«поставь порог 0» не проходит белый список"
    db.run(conn, "UPDATE digest_proposal SET decision = 'accepted', decided_by = %s WHERE id = %s", (uid, p["id"]))
    learning.apply_digest(conn_factory, now=clock.now())
    assert db.setting(conn, "watch_threshold") == 60
    assert db.val(conn, "SELECT kind FROM agent_change ORDER BY id DESC LIMIT 1") == "template"


def test_unknown_target_key_dropped(conn, conn_factory, clock, with_model):
    uid = boss(conn)
    write(conn_factory, uid, clock, SIMILAR)
    fake = FakeLLM({"setting": "crawl_max_pages", "value": 99999})
    learning.build_digest(conn_factory, clock.now(), http=fake.client())
    assert db.val(conn, "SELECT target FROM digest_proposal") == {}


def test_decide_needs_permission(conn, conn_factory, clock, api, no_model):
    c, h, uid = api("recruiter")
    write(conn_factory, uid, clock, SIMILAR)
    learning.build_digest(conn_factory, clock.now())
    pid = c.get("/api/digest/current").json()["proposals"][0]["id"]
    assert c.post(f"/api/digest/{pid}/decide", json={"decision": "accepted"}, headers=h).status_code == 403

