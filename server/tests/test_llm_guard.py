"""T3: вызов модели под флагом назначения и в суточном пределе (S12); тест-замок одной точки вызова."""
import ast
import json
from pathlib import Path

import httpx
import pytest

from hr import config, db, gigachat, llm, quota
from hr.clock import SYSTEM

MSG = [{"role": "user", "content": "{}"}]
HR = Path(__file__).resolve().parent.parent / "hr"


class FakeLLM:
    def __init__(self):
        self.requests = []

    def __call__(self, req):
        self.requests.append(json.loads(req.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": true}'}}]})

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self))


@pytest.fixture
def model(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.test/v1")
    monkeypatch.setenv("LLM_MODEL", "m")
    monkeypatch.setenv("GIGACHAT_AUTH_KEY", "")
    config.get_config.cache_clear()
    gigachat.reset()
    yield FakeLLM()
    config.get_config.cache_clear()


def used(conn, key):
    return db.val(conn, "SELECT used FROM ops.daily_quota WHERE day = %s AND key = %s",
                  (quota.day_of(SYSTEM.now()), key)) or 0


def preset(conn, key, n):
    db.run(conn, "INSERT INTO ops.daily_quota (day, key, used) VALUES (%s, %s, %s)",
           (quota.day_of(SYSTEM.now()), key, n))


def test_s12_flag_on_by_default_model_called_and_counted(conn, conn_factory, model):
    assert db.setting(conn, "llm_assist_enabled") is True
    assert llm.guarded_complete(conn_factory, "explain", MSG, http=model.client()) == {"ok": True}
    assert len(model.requests) == 1 and used(conn, "llm_call") == 1 and used(conn, "llm_synonyms") == 0


def test_s12_flag_off_everything_but_synonyms_disabled(conn, conn_factory, model):
    db.run(conn, "UPDATE settings SET value = 'false' WHERE key = 'llm_assist_enabled'")
    for purpose in ("explain", "draft", "chat", "digest"):
        with pytest.raises(llm.LLMDisabled):
            llm.guarded_complete(conn_factory, purpose, MSG, http=model.client())
    assert model.requests == [] and used(conn, "llm_call") == 0, "выключено — ни вызова, ни списания"
    assert llm.guarded_complete(conn_factory, "synonyms", MSG, http=model.client()) == {"ok": True}
    assert used(conn, "llm_synonyms") == 1


def test_s12_501st_background_call_hits_daily_limit_synonyms_still_pass(conn, conn_factory, model):
    preset(conn, "llm_call", 500)
    with pytest.raises(llm.LLMDailyLimit):
        llm.guarded_complete(conn_factory, "explain", MSG, http=model.client())
    assert model.requests == []
    assert llm.guarded_complete(conn_factory, "synonyms", MSG, http=model.client()) == {"ok": True}
    assert len(model.requests) == 1


def test_s12_synonyms_own_limit_does_not_block_explanations(conn, conn_factory, model):
    preset(conn, "llm_synonyms", 100)
    with pytest.raises(llm.LLMDailyLimit):
        llm.guarded_complete(conn_factory, "synonyms", MSG, http=model.client())
    assert llm.guarded_complete(conn_factory, "explain", MSG, http=model.client()) == {"ok": True}


def test_s12_model_not_configured_spends_nothing(conn, conn_factory, monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "")
    monkeypatch.setenv("GIGACHAT_AUTH_KEY", "")
    config.get_config.cache_clear()
    with pytest.raises(llm.LLMUnavailable):
        llm.guarded_complete(conn_factory, "synonyms", MSG)
    config.get_config.cache_clear()
    assert used(conn, "llm_synonyms") == 0


def test_s12_limit_errors_fall_back_like_unavailable():
    assert issubclass(llm.LLMDisabled, llm.LLMUnavailable) and issubclass(llm.LLMDailyLimit, llm.LLMUnavailable)


def _complete_calls(path: Path) -> list[str]:
    """Обращения к complete модуля llm: `llm.complete(...)` и `from .llm import complete`."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "complete" \
                and isinstance(node.value, ast.Name) and node.value.id == "llm":
            found.append(f"{path.name}:{node.lineno}")
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("llm") \
                and any(a.name == "complete" for a in node.names):
            found.append(f"{path.name}:{node.lineno}")
    return found


def test_s12_lock_llm_complete_only_from_llm_py():
    offenders = [hit for p in sorted(HR.rglob("*.py")) if p.name != "llm.py" for hit in _complete_calls(p)]
    assert offenders == [], "модель зовётся мимо llm.guarded_complete"
