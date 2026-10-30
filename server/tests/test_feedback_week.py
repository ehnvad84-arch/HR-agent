"""T19 manager-cabinet: недельная сводка всех замечаний — в Телеграм и на почту (S21)."""
import httpx

from hr import config, db, learning, tasks, telegram

CRIT = "Иванов И. И. с телефоном +7 900 123-45-67 попал в выдачу, хотя у него нет опыта B2B продаж вообще."
REC = "Рекомендую добавлять в портрет инженера слово PCIe: многие сильные кандидаты его указывают в резюме."


def add(conn, author, tag, body, clock):
    db.run(conn, "INSERT INTO feedback_message (author_id, tag, body, created_at) VALUES (%s, %s, %s, %s)",
           (author, tag, body, clock.now()))


def test_week_text_all_authors_masked(conn, clock):
    a = db.val(conn, "INSERT INTO app_user (login, full_name, role) VALUES ('a', 'Петрова А.', 'recruiter') "
                     "RETURNING id")
    b = db.val(conn, "INSERT INTO app_user (login, full_name, role) VALUES ('b', 'Сидоров С.', 'hiring_manager') "
                     "RETURNING id")
    add(conn, a, "criticism", CRIT, clock)
    add(conn, b, "recommendation", REC, clock)
    text, n = learning.week_text(conn, clock.now())
    assert n == 2 and "Петрова А." in text and "Сидоров С." in text and "PCIe" in text
    assert "123-45-67" not in text and "Иванов И. И." not in text
    assert "Критика — 1" in text and "Рекомендация — 1" in text


def test_sends_to_telegram_or_notes_no_channel(conn, conn_factory, clock, monkeypatch):
    a = db.val(conn, "INSERT INTO app_user (login, full_name, role) VALUES ('a', 'Петрова А.', 'recruiter') "
                     "RETURNING id")
    add(conn, a, "remark", REC, clock)
    monkeypatch.delenv("FEEDBACK_TG_TOKEN", raising=False)
    monkeypatch.delenv("SMTP_HOST", raising=False)
    config.get_config.cache_clear()
    st = learning.feedback_week(conn_factory, clock=clock)
    assert st.note.startswith("канал не настроен")
    sent = []

    def handler(req: httpx.Request):
        sent.append(req)
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setenv("FEEDBACK_TG_TOKEN", "123:abc")
    monkeypatch.setenv("FEEDBACK_TG_CHAT", "42")
    config.get_config.cache_clear()
    st = learning.feedback_week(conn_factory, clock=clock, http=httpx.Client(transport=httpx.MockTransport(handler)))
    assert st.note == "отправлено: Телеграм" and len(sent) == 1
    assert b'"chat_id":"42"' in sent[0].content.replace(b" ", b"") and "feedback_week" in tasks.TASKS
    config.get_config.cache_clear()


def test_chunks_split_long_text():
    parts = telegram.chunks("строка\n" * 1500)
    assert len(parts) > 1 and all(len(p) <= telegram.LIMIT for p in parts)


def test_telegram_error_hides_token_and_mail_still_tried(conn, conn_factory, clock, monkeypatch):
    from hr import mailer
    a = db.val(conn, "INSERT INTO app_user (login, full_name, role) VALUES ('a', 'А', 'recruiter') RETURNING id")
    add(conn, a, "remark", REC, clock)
    monkeypatch.setenv("FEEDBACK_TG_TOKEN", "SECRET123:tok")
    monkeypatch.setenv("FEEDBACK_TG_CHAT", "42")
    config.get_config.cache_clear()
    tried = []
    monkeypatch.setattr(mailer, "send", lambda *a, **k: tried.append(1) or [])

    def boom(req):
        raise httpx.ConnectError("fail https://api.telegram.org/botSECRET123:tok/sendMessage")

    st = learning.feedback_week(conn_factory, clock=clock, http=httpx.Client(transport=httpx.MockTransport(boom)))
    assert tried and st.note == "отправлено: почта"
    try:
        telegram.send("x", http=httpx.Client(transport=httpx.MockTransport(boom)))
    except Exception as e:  # noqa: BLE001
        assert "SECRET123" not in str(e)
    config.get_config.cache_clear()
