"""T11 workplace: черновик письма, правка, одобрение, отказ; «Напомнить» у зависшего (S18, S37; пункты 9, 10)."""
import json

import httpx
import pytest
from factories import hh_item, profile, source

from hr import config, db, decisions, gigachat
from hr.normalize import normalize
from hr.pipeline import process


@pytest.fixture
def cand(conn, clock):
    prof = profile(conn)
    vid = db.val(conn, "INSERT INTO vacancy (role_profile_id, title) VALUES (%s, %s) RETURNING id",
                 (prof["id"], prof["title"]))
    it = hh_item(last_name="Секретов", first_name="Иван")
    pid = process(conn, source(conn, "hh"), normalize("hh", it), it, clock.now()).person_id
    uid = db.val(conn, "INSERT INTO app_user (login, full_name, role) VALUES ('t', 'Т', 'recruiter') RETURNING id")
    return decisions.take(conn, pid, uid, clock.now()), vid


class FakeLLM:
    def __init__(self, text="Здравствуйте, {имя}! Видим ваш опыт в схемотехнике и приглашаем поговорить."):
        self.text, self.requests = text, []

    def __call__(self, req):
        self.requests.append(json.loads(req.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"text": self.text},
                                                                                         ensure_ascii=False)}}]})

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self))


@pytest.fixture
def with_llm(monkeypatch):
    from hr.deps import get_llm_http
    from hr.main import app
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.test/v1")
    monkeypatch.setenv("LLM_MODEL", "m")
    monkeypatch.setenv("GIGACHAT_AUTH_KEY", "")
    config.get_config.cache_clear()
    gigachat.reset()
    fake = FakeLLM()
    app.dependency_overrides[get_llm_http] = fake.client
    yield fake
    config.get_config.cache_clear()


def test_template_draft_without_model(conn, api, cand, monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "")
    monkeypatch.setenv("GIGACHAT_AUTH_KEY", "")
    config.get_config.cache_clear()
    cid, vid = cand
    c, h, _ = api()
    r = c.post(f"/api/candidate/{cid}/draft", json={"vacancy_id": vid, "kind": "first_message"}, headers=h)
    assert r.status_code == 200 and r.json()["by"] == "template"
    assert "Иван" in r.json()["text"] and "Ведущий инженер-электронщик" in r.json()["text"]
    assert db.val(conn, "SELECT state::text FROM action_queue WHERE id = %s", (r.json()["action_id"],)) == "proposed"


def test_model_draft_gets_no_name_code_inserts_it(conn, api, cand, with_llm):
    cid, vid = cand
    c, h, _ = api()
    r = c.post(f"/api/candidate/{cid}/draft", json={"vacancy_id": vid, "kind": "first_message"}, headers=h)
    assert r.json()["by"] == "model" and r.json()["text"].startswith("Здравствуйте, Иван!")
    sent = json.dumps(with_llm.requests[0], ensure_ascii=False)
    assert "Секретов" not in sent and "Иван" not in sent and "900 000" not in sent and "@" not in sent


def test_model_draft_with_link_falls_back(conn, api, cand, with_llm):
    with_llm.text = "Пишите на hr@evil.example или https://evil.example"
    cid, vid = cand
    c, h, _ = api()
    r = c.post(f"/api/candidate/{cid}/draft", json={"vacancy_id": vid, "kind": "first_message"}, headers=h)
    assert r.json()["by"] == "template" and "evil" not in r.json()["text"]


def test_s18_approve_saves_edited_text_and_moves_status(conn, api, cand):
    cid, vid = cand
    c, h, uid = api()
    aid = c.post(f"/api/candidate/{cid}/draft", json={"vacancy_id": vid, "kind": "first_message"},
                 headers=h).json()["action_id"]
    r = c.post(f"/api/action/{aid}/approve", json={"text": "Иван, добрый день! Мой вариант письма."}, headers=h)
    assert r.status_code == 200 and r.json()["state"] == "executed"
    a = db.one(conn, "SELECT state::text AS state, payload, decided_by FROM action_queue WHERE id = %s", (aid,))
    assert a["payload"]["final_text"] == "Иван, добрый день! Мой вариант письма." and a["payload"]["edited"]
    assert a["decided_by"] == uid
    assert db.val(conn, "SELECT status::text FROM candidate WHERE id = %s", (cid,)) == "first_message_sent"
    assert db.val(conn, "SELECT count(*) FROM status_history WHERE candidate_id = %s AND to_status = "
                        "'first_message_sent'", (cid,)) == 1
    assert c.post(f"/api/action/{aid}/approve", json={}, headers=h).status_code == 409, "дважды не исполняется"


def test_decline_with_reason(conn, api, cand):
    cid, vid = cand
    c, h, _ = api()
    aid = c.post(f"/api/candidate/{cid}/draft", json={"vacancy_id": vid, "kind": "first_message"},
                 headers=h).json()["action_id"]
    assert c.post(f"/api/action/{aid}/decline", json={"reason": " "}, headers=h).status_code == 422
    r = c.post(f"/api/action/{aid}/decline", json={"reason": "тон слишком официальный"}, headers=h)
    assert r.json()["state"] == "declined"
    assert db.val(conn, "SELECT decline_reason FROM action_queue WHERE id = %s", (aid,)) == "тон слишком официальный"


def test_s37_follow_up_draft(conn, api, cand):
    cid, vid = cand
    c, h, _ = api()
    r = c.post(f"/api/candidate/{cid}/draft", json={"vacancy_id": vid, "kind": "follow_up"}, headers=h)
    assert r.status_code == 200 and r.json()["text"]


def test_no_basis_blocks_draft(conn, api, cand):
    cid, vid = cand
    db.run(conn, "UPDATE consent SET basis = 'not_obtained' WHERE candidate_id = %s", (cid,))
    c, h, _ = api()
    r = c.post(f"/api/candidate/{cid}/draft", json={"vacancy_id": vid, "kind": "first_message"}, headers=h)
    assert r.status_code == 409


def test_get_action_gives_draft_text_to_the_window(conn, api, cand):
    """Окно черновика на «Сегодня» берёт текст и «по шаблону» из действия; чужих полей payload в ответе нет."""
    cid, vid = cand
    c, h, uid = api()
    made = c.post(f"/api/candidate/{cid}/draft", json={"vacancy_id": vid, "kind": "first_message"}, headers=h).json()
    r = c.get(f"/api/action/{made['action_id']}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == made["action_id"] and body["kind"] == "first_message" and body["state"] == "proposed"
    assert body["candidate_id"] == cid
    assert body["payload"] == {"text": made["text"], "by": "template"}
    assert "by_user" not in r.text


def test_get_action_after_decision_shows_state(conn, api, cand):
    cid, vid = cand
    c, h, _ = api()
    made = c.post(f"/api/candidate/{cid}/draft", json={"vacancy_id": vid, "kind": "follow_up"}, headers=h)
    aid = made.json()["action_id"]
    c.post(f"/api/action/{aid}/decline", json={"reason": "не сейчас"}, headers=h)
    assert c.get(f"/api/action/{aid}").json()["state"] == "declined"


def test_get_action_unknown_and_forbidden(conn, api, cand):
    cid, vid = cand
    c, h, _ = api()
    assert c.get("/api/action/999999").status_code == 404
    made = c.post(f"/api/candidate/{cid}/draft", json={"vacancy_id": vid, "kind": "first_message"}, headers=h)
    aid = made.json()["action_id"]
    manager, _, _ = api("hiring_manager")
    assert manager.get(f"/api/action/{aid}").status_code == 403
