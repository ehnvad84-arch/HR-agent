"""T17 workplace: чат обучения — от 40 знаков, маскирование перед моделью, ответ агента (S29; W3)."""
import json

import httpx
import pytest

from hr import config, db, gigachat

MSG = "Письма для ОТК звучат как для продажников, люди на производстве такого тона не любят."


class FakeLLM:
    def __init__(self, text="Понял: для ОТК нужен более спокойный тон. Учту в сводке."):
        self.text, self.requests = text, []

    def __call__(self, req):
        self.requests.append(json.loads(req.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"text": self.text},
                                                                                         ensure_ascii=False)}}]})

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self))


@pytest.fixture
def model(monkeypatch):
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


def send(c, h, body=MSG, tag="criticism"):
    return c.post("/api/feedback", json={"body": body, "tag": tag}, headers=h)


def test_s29_message_and_template_reply(conn, api, monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "")
    monkeypatch.setenv("GIGACHAT_AUTH_KEY", "")
    config.get_config.cache_clear()
    c, h, uid = api()
    r = send(c, h)
    assert r.status_code == 200 and r.json()["by"] == "template" and "сводк" in r.json()["reply"]
    feed = c.get("/api/feedback").json()["items"]
    assert feed[0]["body"] == MSG and feed[0]["reply"] == r.json()["reply"] and feed[0]["author"]
    assert db.val(conn, "SELECT author_id FROM feedback_message WHERE id = %s", (r.json()["id"],)) == uid


def test_short_message_rejected(api):
    c, h, _ = api()
    r = send(c, h, body="коротко")
    assert r.status_code == 422 and "40" in r.json()["message"]


def test_model_reply(conn, api, model):
    c, h, _ = api()
    r = send(c, h)
    assert r.json()["by"] == "model" and "ОТК" in r.json()["reply"]


def test_w3_names_and_contacts_masked_before_model(conn, api, model):
    db.run(conn, """INSERT INTO candidate (fio, retention_until) VALUES ('Скворцов Пётр Ильич', current_date + 100)""")
    c, h, _ = api()
    send(c, h, body="Скворцову звонили дважды, телефон +7 918 123-45-67, почта skv@mail.ru — тон писем не тот.")
    sent = json.dumps(model.requests[0], ensure_ascii=False)
    assert "Скворц" not in sent and "918" not in sent and "skv@" not in sent and "[имя]" in sent


def test_w3_unsure_name_goes_template(conn, api, model):
    c, h, _ = api()
    r = send(c, h, body="Иванов И. И. опять не ответил, давайте менять подход к первому письму кандидатам.")
    assert r.json()["by"] == "template" and model.requests == []


def test_hiring_manager_cannot_write(api):
    c, h, _ = api("hiring_manager")
    assert send(c, h).status_code == 403
