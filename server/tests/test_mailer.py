"""T19b workplace: почта — дайджест, отчёт, сводка, получатели; без данных о людях (S33, S40, S41; 8, 26)."""
from datetime import timedelta

import pytest
from factories import hh_item, profile, source

from hr import config, db, learning, mailer, tasks
from hr.normalize import normalize
from hr.pipeline import process


class FakeSMTP:
    sent: list = []
    fail = 0

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def ehlo(self):
        pass

    def starttls(self, context=None):
        pass

    def login(self, user, password):
        assert user == "robot@example.ru" and password == "app-pass"

    def send_message(self, msg):
        if FakeSMTP.fail:
            FakeSMTP.fail -= 1
            raise OSError("smtp down")
        FakeSMTP.sent.append(msg)


class FakeSSL(FakeSMTP):
    def __init__(self, host, port, timeout=None, context=None):
        super().__init__(host, port, timeout)


@pytest.fixture(autouse=True)
def smtp(monkeypatch):
    FakeSMTP.sent, FakeSMTP.fail = [], 0
    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", FakeSSL)
    for k, v in {"SMTP_HOST": "smtp.example.ru", "SMTP_PORT": "587", "SMTP_USER": "robot@example.ru",
                 "SMTP_PASSWORD": "app-pass", "MAIL_TO": "boss@example.ru, director@example.ru"}.items():
        monkeypatch.setenv(k, v)
    config.get_config.cache_clear()
    yield FakeSMTP
    config.get_config.cache_clear()


def body_of(msg):
    return msg.get_content()


def people(conn, clock):
    profile(conn)
    it = hh_item(last_name="Секретов", first_name="Иван")
    pid = process(conn, source(conn, "hh"), normalize("hh", it), it, clock.now()).person_id
    db.run(conn, """INSERT INTO alert (person_id, reason, headline) VALUES (%s, 'new_high_score',
                    'Новый в поле зрения: Секретов')""", (pid,))


def test_digest_mail_numbers_only(conn, conn_factory, clock, smtp):
    people(conn, clock)
    st = mailer.digest_mail(conn_factory, clock=clock)
    assert st.changed == 1 and len(smtp.sent) == 1
    msg = smtp.sent[0]
    assert msg["To"] == "boss@example.ru, director@example.ru" and msg["From"] == "robot@example.ru"
    text = body_of(msg)
    assert "сигналов «обратите внимание»: 2" in text and "localhost:8000" in text
    assert "Секретов" not in text and "Иван" not in text and "918" not in text
    assert "digest_mail" in tasks.TASKS and "weekly_report" in tasks.TASKS


def test_weekly_report_sent_and_marked(conn, conn_factory, clock, api, smtp):
    c, h, uid = api()
    db.run(conn, "INSERT INTO agent_change (kind, applied_by, applied_at, note) VALUES ('threshold', %s, %s, 'п')",
           (uid, clock.now() - timedelta(days=1)))
    r = c.post("/api/report/weekly/send", json={}, headers=h)
    assert r.status_code == 200 and r.json()["sent_at"]
    rep = db.one(conn, "SELECT sent_at, recipients FROM weekly_report ORDER BY id DESC LIMIT 1")
    assert rep["sent_at"] and rep["recipients"] == ["boss@example.ru", "director@example.ru"]
    assert "Пороги" in body_of(smtp.sent[0])


def test_send_retries_once_then_fails_keeping_report(conn, conn_factory, clock, api, smtp):
    smtp.fail = 2
    c, h, _ = api()
    r = c.post("/api/report/weekly/send", json={}, headers=h)
    assert r.status_code == 502 and "не отправлено" in r.json()["message"]
    rep = db.one(conn, "SELECT sent_at FROM weekly_report ORDER BY id DESC LIMIT 1")
    assert rep is not None and rep["sent_at"] is None, "отчёт сохранён, отметки об отправке нет"
    assert clock.slept == [60]


def test_retry_succeeds(conn_factory, clock, smtp, conn):
    smtp.fail = 1
    assert mailer.digest_mail(conn_factory, clock=clock).changed == 1 and len(smtp.sent) == 1


def test_s40_recipients(api):
    c, _, _ = api()
    assert c.get("/api/report/recipients").json()["recipients"] == ["boss@example.ru", "director@example.ru"]


def test_s41_digest_to_mail(conn, conn_factory, clock, api, smtp, monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "")
    monkeypatch.setenv("GIGACHAT_AUTH_KEY", "")
    config.get_config.cache_clear()
    c, h, uid = api()
    learning.post(conn_factory, uid, "Письма для ОТК звучат как для продажников, людям на производстве это не "
                  "нравится.", "criticism", clock.now())
    learning.build_digest(conn_factory, clock.now())
    r = c.post("/api/digest/current/send", json={}, headers=h)
    assert r.status_code == 200 and "1 предлож" in body_of(smtp.sent[0])


def test_not_configured(monkeypatch, conn_factory, clock):
    monkeypatch.setenv("SMTP_HOST", "")
    config.get_config.cache_clear()
    st = mailer.digest_mail(conn_factory, clock=clock)
    assert st.changed == 0 and "SMTP" in st.note
