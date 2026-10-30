"""T12 workplace: открытие контакта hh — подготовка без списания, подтверждение, аудит (S16, S17; пункт 11).
Только на заглушке hh: живое подтверждение делает человек (B1)."""
import httpx
import psycopg
import pytest
from cryptography.fernet import Fernet
from factories import hh_item, profile, source

from hr import config, db, decisions, hh
from hr.normalize import normalize
from hr.pipeline import process

RID = "a1b2c3d4e5"
CONTACTS = [{"kind": "phone", "type": {"id": "cell"}, "contact_value": "+7 (918) 111-22-33", "preferred": True},
            {"kind": "email", "type": {"id": "email"}, "contact_value": "kandidat@example.ru", "preferred": False}]


@pytest.fixture(autouse=True)
def hh_env(monkeypatch, conn, clock):
    monkeypatch.setenv("SECRETS_KEY", Fernet.generate_key().decode())
    config.get_config.cache_clear()
    hh.store_tokens(conn, {"access_token": "AT", "refresh_token": "RT", "expires_in": 30 * 86400}, "777", clock.now())


class FakeHH:
    def __init__(self, status="NONE", contact_host="api.hh.ru", fail=None):
        self.status, self.contact_host, self.fail, self.calls = status, contact_host, fail, []

    def __call__(self, req: httpx.Request):
        self.calls.append(str(req.url))
        if self.fail:
            return httpx.Response(429, json={"errors": [{"type": "resumes", "value": self.fail}]})
        if req.url.params.get("with_contact"):
            return httpx.Response(200, json={"id": RID, "contact_view_status": "FULL", "contact": CONTACTS})
        body = {"id": RID, "contact_view_status": self.status, "hidden_fields": [],
                "actions": {"get_with_contact": {"url": f"https://{self.contact_host}/resumes/{RID}?with_contact=abc"}}}
        if self.status == "FULL":
            body["contact"] = CONTACTS
        return httpx.Response(200, json=body)

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self))


@pytest.fixture
def cand(conn, clock):
    profile(conn)
    it = hh_item(id=RID, hidden_fields=[])
    it.pop("contact", None)
    pid = process(conn, source(conn, "hh"), normalize("hh", it), it, clock.now()).person_id
    uid = db.val(conn, "INSERT INTO app_user (login, full_name, role) VALUES ('t', 'Т', 'recruiter') RETURNING id")
    return decisions.take(conn, pid, uid, clock.now())


def client_with(api, fake, role="head_recruiter"):
    from hr.deps import get_hh_http
    from hr.main import app
    app.dependency_overrides[get_hh_http] = fake.client
    return api(role)


def prepare(c, h, cid):
    return c.post(f"/api/candidate/{cid}/contact/prepare", json={}, headers=h)


def test_s16_prepare_spends_nothing(conn, api, cand):
    fake = FakeHH()
    c, h, _ = client_with(api, fake)
    r = prepare(c, h, cand)
    assert r.status_code == 200 and r.json()["will_spend"] is True and r.json()["hidden"] == []
    assert fake.calls == [], "подготовка к hh не ходит"
    assert db.val(conn, "SELECT kind::text FROM action_queue WHERE id = %s", (r.json()["action_id"],)) == "open_contact"


def test_s16_open_contact_stores_and_audits(conn, api, cand):
    fake = FakeHH()
    c, h, uid = client_with(api, fake)
    aid = prepare(c, h, cand).json()["action_id"]
    r = c.post(f"/api/action/{aid}/approve", json={}, headers=h)
    assert r.status_code == 200 and r.json()["state"] == "executed" and r.json()["result"]["contacts"] == 2
    assert "918" not in r.text and "kandidat" not in r.text
    kinds = {x["kind"] for x in db.rows(conn, "SELECT kind FROM contact WHERE candidate_id = %s", (cand,))}
    assert kinds == {"phone", "email"}
    assert db.val(conn, "SELECT count(*) FROM audit_log WHERE action = 'view_contact' AND user_id = %s", (uid,)) == 1
    assert any("with_contact=abc" in u for u in fake.calls)
    shown = c.get(f"/api/candidate/{cand}/contacts")
    assert "kandidat@example.ru" in shown.text
    assert db.val(conn, "SELECT count(*) FROM audit_log WHERE action = 'view_contact' AND user_id = %s", (uid,)) == 2
    payload = db.val(conn, "SELECT payload::text FROM action_queue WHERE id = %s", (aid,))
    assert "918" not in payload and "kandidat" not in payload


def test_already_full_no_spend(conn, api, cand):
    fake = FakeHH(status="FULL")
    c, h, _ = client_with(api, fake)
    aid = prepare(c, h, cand).json()["action_id"]
    c.post(f"/api/action/{aid}/approve", json={}, headers=h)
    assert not any("with_contact" in u for u in fake.calls), "контакт уже доступен — без списания"
    assert db.val(conn, "SELECT count(*) FROM contact WHERE candidate_id = %s", (cand,)) == 2


def test_s17_hidden_contacts_blocked_before_spend(conn, api, cand):
    db.run(conn, """UPDATE observation SET raw = jsonb_set(raw, '{hidden_fields}',
                    '[{"id": "phones"}, {"id": "email"}, {"id": "other_contacts"}]') WHERE external_id = %s""", (RID,))
    fake = FakeHH()
    c, h, _ = client_with(api, fake)
    r = prepare(c, h, cand)
    assert r.json()["hidden"] == ["phones", "email", "other_contacts"] and r.json()["will_spend"] is False
    assert c.post(f"/api/action/{r.json()['action_id']}/approve", json={}, headers=h).status_code == 409
    assert fake.calls == []


def test_foreign_contact_host_refused(conn, api, cand):
    fake = FakeHH(contact_host="evil.example")
    c, h, _ = client_with(api, fake)
    aid = prepare(c, h, cand).json()["action_id"]
    r = c.post(f"/api/action/{aid}/approve", json={}, headers=h)
    assert r.status_code == 502 and not any("evil.example" in u for u in fake.calls)
    assert db.val(conn, "SELECT state::text FROM action_queue WHERE id = %s", (aid,)) == "proposed"


def test_s17_limit_keeps_action_proposed(conn, api, cand):
    fake = FakeHH(fail="view_limit_exceeded")
    c, h, _ = client_with(api, fake)
    aid = prepare(c, h, cand).json()["action_id"]
    r = c.post(f"/api/action/{aid}/approve", json={}, headers=h)
    assert r.status_code == 503 and "лимит" in r.json()["message"]
    assert db.val(conn, "SELECT state::text FROM action_queue WHERE id = %s", (aid,)) == "proposed"


def test_double_approve_no_double_spend(conn, api, cand):
    fake = FakeHH()
    c, h, _ = client_with(api, fake)
    aid = prepare(c, h, cand).json()["action_id"]
    c.post(f"/api/action/{aid}/approve", json={}, headers=h)
    n = len(fake.calls)
    assert c.post(f"/api/action/{aid}/approve", json={}, headers=h).status_code == 409
    assert len(fake.calls) == n
    aid2 = prepare(c, h, cand).json()
    assert aid2["already_open"] is True and aid2["will_spend"] is False


def test_hiring_manager_cannot_open(conn, api, cand):
    c, h, _ = client_with(api, FakeHH(), role="hiring_manager")
    assert prepare(c, h, cand).status_code == 403
    assert c.get(f"/api/candidate/{cand}/contacts").status_code == 403


def test_busy_during_hh_collect(conn, api, cand):
    from hr.config import get_config
    fake = FakeHH()
    c, h, _ = client_with(api, fake)
    aid = prepare(c, h, cand).json()["action_id"]
    with psycopg.connect(get_config().database_url, autocommit=True) as lock:
        lock.execute("SELECT pg_advisory_lock(hashtextextended('source:hh', 0))")
        r = c.post(f"/api/action/{aid}/approve", json={}, headers=h)
    assert r.status_code == 409 and "сбор" in r.json()["message"] and fake.calls == []


def test_repeated_prepare_reuses_the_waiting_action(conn, api, cand):
    """«Открыть контакт» и «Отмена» несколько раз подряд не плодят действия в «Ждут подтверждения»."""
    c, h, _ = client_with(api, FakeHH())
    first = prepare(c, h, cand).json()["action_id"]
    assert prepare(c, h, cand).json()["action_id"] == first
    rows = db.rows(conn, "SELECT id FROM action_queue WHERE candidate_id = %s AND kind = 'open_contact'", (cand,))
    assert len(rows) == 1


def test_prepare_remembers_spend_for_the_queue_card(conn, api, cand):
    """Карточка в «Сегодня» знает, спишется ли контакт: решение берётся из payload, без нового запроса prepare."""
    c, h, _ = client_with(api, FakeHH())
    aid = prepare(c, h, cand).json()["action_id"]
    shown = c.get(f"/api/action/{aid}").json()
    assert shown["kind"] == "open_contact"
    assert shown["payload"] == {"hidden": [], "blocked": False, "will_spend": True, "already_open": False}
    assert "resume_id" not in str(shown) and RID not in str(shown)
