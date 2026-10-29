"""T7 workplace: переходник GigaChat — токен, один поток, пауза, сертификат (S7, S8)."""
import json
import threading
import time

import httpx
import pytest

from hr import config, gigachat, llm


@pytest.fixture(autouse=True)
def giga_env(monkeypatch):
    monkeypatch.setenv("GIGACHAT_AUTH_KEY", "Y2xpZW50OnNlY3JldA==")
    monkeypatch.delenv("GIGACHAT_CA_FILE", raising=False)
    config.get_config.cache_clear()
    gigachat.reset()
    yield
    gigachat.reset()
    config.get_config.cache_clear()


class FakeGiga:
    def __init__(self, chat_status=200, expires_in=1800, first_401=False):
        self.chat_status, self.expires_in, self.first_401 = chat_status, expires_in, first_401
        self.calls, self.n = [], 0

    def __call__(self, req: httpx.Request):
        self.calls.append((req.url.host, req.url.path))
        if req.url.path == "/api/v2/oauth":
            assert req.headers["Authorization"].startswith("Basic ") and req.headers["RqUID"]
            assert b"scope=GIGACHAT_API_PERS" in req.content
            self.n += 1
            return httpx.Response(200, json={"access_token": f"tok{self.n}",
                                             "expires_at": int(time.time() + self.expires_in) * 1000})
        assert req.url.host == "api.giga.chat" and req.url.path == "/v1/chat/completions"
        if self.first_401 and req.headers["Authorization"] == "Bearer tok1":
            return httpx.Response(401)
        if self.chat_status != 200:
            return httpx.Response(self.chat_status)
        body = json.loads(req.content)
        assert body["model"] == "GigaChat-2" and "response_format" not in body
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": true}'}}]})

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self))

    def oauth_calls(self):
        return [c for c in self.calls if c[1] == "/api/v2/oauth"]


MSG = [{"role": "user", "content": "проверка"}]


def test_complete_through_gigachat_and_token_cached():
    fake = FakeGiga()
    assert llm.complete(MSG, http=fake.client()) == {"ok": True}
    assert llm.complete(MSG, http=fake.client()) == {"ok": True}
    assert len(fake.oauth_calls()) == 1, "токен на 30 минут переиспользуется"


def test_token_refreshed_before_expiry():
    fake = FakeGiga(expires_in=30)   # меньше минуты до конца — обновить
    llm.complete(MSG, http=fake.client())
    llm.complete(MSG, http=fake.client())
    assert len(fake.oauth_calls()) == 2


def test_401_refreshes_token_once():
    fake = FakeGiga(first_401=True)
    assert llm.complete(MSG, http=fake.client()) == {"ok": True}
    assert len(fake.oauth_calls()) == 2


def test_429_pauses_without_further_calls():
    fake = FakeGiga(chat_status=429)
    with pytest.raises(llm.LLMUnavailable):
        llm.complete(MSG, http=fake.client())
    n = len(fake.calls)
    with pytest.raises(llm.LLMUnavailable, match="пауз"):
        llm.complete(MSG, http=fake.client())
    assert len(fake.calls) == n, "на паузе к GigaChat не ходим"


def test_single_thread_busy(monkeypatch):
    monkeypatch.setattr(gigachat, "BUSY_WAIT_S", 0.05)
    started, release = threading.Event(), threading.Event()

    def slow(req):
        if req.url.path == "/api/v2/oauth":
            return httpx.Response(200, json={"access_token": "t", "expires_at": int(time.time()) + 1800})
        started.set()
        release.wait(2)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    http = httpx.Client(transport=httpx.MockTransport(slow))
    t = threading.Thread(target=lambda: llm.complete(MSG, http=http))
    t.start()
    started.wait(2)
    with pytest.raises(llm.LLMUnavailable, match="занята"):
        llm.complete(MSG, http=http)
    release.set()
    t.join()


def test_bad_ca_file_is_unavailable(monkeypatch, tmp_path):
    bad = tmp_path / "ca.pem"
    bad.write_text("не сертификат")
    monkeypatch.setenv("GIGACHAT_CA_FILE", str(bad))
    config.get_config.cache_clear()
    with pytest.raises(llm.LLMUnavailable, match="сертификат"):
        llm.complete(MSG)


def test_token_not_in_error_text():
    def boom(req):
        if req.url.path == "/api/v2/oauth":
            return httpx.Response(200, json={"access_token": "СЕКРЕТНЫЙ", "expires_at": int(time.time()) + 1800})
        return httpx.Response(500, text="СЕКРЕТНЫЙ")

    with pytest.raises(llm.LLMUnavailable) as e:
        llm.complete(MSG, http=httpx.Client(transport=httpx.MockTransport(boom)))
    assert "СЕКРЕТНЫЙ" not in str(e.value)


def test_disabled_without_key(monkeypatch):
    monkeypatch.setenv("GIGACHAT_AUTH_KEY", "")
    monkeypatch.setenv("LLM_BASE_URL", "")
    config.get_config.cache_clear()
    assert not config.get_config().llm_enabled
