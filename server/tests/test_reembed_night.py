"""T12 semantic-search: ночной пересчёт векторов базы по частям (S10)."""
import re
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from factories import hh_item, profile, source
from test_semantic import FakeBGE

from hr import config, db, embed, load_check, search, tasks
from hr.clock import FakeClock
from hr.normalize import normalize
from hr.pipeline import process

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "install_autostart.sh"
NIGHT = datetime(2026, 10, 25, 23, 0, tzinfo=UTC)   # 02:00 по Москве


@pytest.fixture
def night():
    return FakeClock(NIGHT)


@pytest.fixture
def base(conn, night):
    """Профиль и трое людей с векторами hash-v1 — после подключения bge-m3 все трое без вектора текущей модели."""
    pr = profile(conn)
    ids = []
    for key, last in (("p1", "Первый"), ("p2", "Второй"), ("p3", "Третий")):
        exp = [{"company": f"Завод {key}", "position": "Ведущий инженер-электронщик", "start": "2019-01-01",
                "end": None}]
        it = hh_item(id=key, last_name=last, experience=exp)
        process(conn, source(conn, "hh"), normalize("hh", it), it, night.now())
        ids.append(db.val(conn, "SELECT person_id FROM observation WHERE external_id = %s", (key,)))
    db.run(conn, "UPDATE settings SET value = '1' WHERE key = 'embed_batch_size'")
    return {"profile": pr, "people": ids}


@pytest.fixture
def bge(monkeypatch):
    monkeypatch.setenv("EMBED_BASE_URL", "http://emb.test/v1")
    monkeypatch.setenv("EMBED_MODEL", "BAAI/bge-m3")
    config.get_config.cache_clear()
    fake = FakeBGE()
    monkeypatch.setattr(embed, "client", fake.client)
    yield fake
    config.get_config.cache_clear()


def with_vector(conn):
    return {r["person_id"] for r in db.rows(conn, "SELECT person_id FROM person_embedding WHERE embed_model = %s",
                                            ("BAAI/bge-m3",))}


def counts(conn):
    return (db.val(conn, "SELECT count(*) FROM obs_event"), db.val(conn, "SELECT count(*) FROM alert"))


def test_s10_night_profiles_then_people_and_score_without_signals(conn, conn_factory, base, bge, night):
    before = counts(conn)
    st = search.reembed_night(conn_factory, clock=night)
    assert bge.texts[0].startswith("Ведущий инженер-электронщик."), "сначала вектор профиля"
    assert with_vector(conn) == set(base["people"]) and st.changed == 3 and st.errors == 0
    for pid in base["people"]:
        f = db.val(conn, "SELECT features FROM match WHERE person_id = %s", (pid,))["semantic"]
        assert f["sim"] == 0.82 and "skipped" not in f, "балл пачки пересчитан с признаком смысла"
    assert counts(conn) == before, "пересчёт по векторам не создаёт событий и сигналов"


def test_s10_missing_before_stale(conn, conn_factory, base, bge, night):
    search.reembed_night(conn_factory, clock=night)
    stale, missing = base["people"][0], base["people"][1]
    db.run(conn, "UPDATE person SET last_event_at = now() + interval '1 day' WHERE id = %s", (stale,))
    db.run(conn, "DELETE FROM person_embedding WHERE person_id = %s", (missing,))
    order = search.pending_people(conn, embed.method())
    assert order == [missing, stale], "люди без вектора — раньше устаревших"


def test_s10_interrupted_continues_next_night(conn, conn_factory, base, bge, night, monkeypatch):
    def slow(req):   # каждый запрос к bge-m3 — два часа
        night.advance(7200)
        return bge(req)

    monkeypatch.setattr(embed, "client", lambda: httpx.Client(transport=httpx.MockTransport(slow)))
    st = search.reembed_night(conn_factory, clock=night)
    done = with_vector(conn)
    assert len(done) == 2 and st.changed == 2, "02:00 профиль, 04:00 и 06:00 люди, в 08:00 — стоп (после 06:30)"
    assert "06:30" in (st.note or "")
    night.advance((datetime(2026, 10, 26, 23, 0, tzinfo=UTC) - night.now()).total_seconds())   # следующая ночь, 02:00
    search.reembed_night(conn_factory, clock=night)
    assert with_vector(conn) == set(base["people"]), "следующей ночью досчитаны оставшиеся"


def test_s10_three_failures_in_a_row_stop(conn, conn_factory, base, bge, night):
    bge.status = 500
    st = search.reembed_night(conn_factory, clock=night)
    assert len(bge.texts) == 3, "профиль и две пачки людей — три отказа подряд, дальше служба не вызывается"
    assert st.errors == 3 and not with_vector(conn)
    assert embed.down_since(conn) is not None


def test_s10_score_task_takes_only_one_batch(conn, conn_factory, base, bge, night):
    search.embed_profile(conn_factory, base["profile"]["id"], now=night.now())
    bge.texts.clear()
    st = tasks.score(conn_factory, clock=night)
    assert st.extra["vectors"] == 1 and len(with_vector(conn)) == 1 and len(bge.texts) == 1


def test_s10_reembed_night_registered_scheduled_and_has_timer():
    assert tasks.TASKS["reembed_night"].locks == ("embed",)
    days, hh, mm = load_check.SCHEDULE["reembed_night"]
    text = SCRIPT.read_text()
    assert "hr.cli run reembed_night" in text
    timer = re.search(r'ruware-reembed-night\.timer" <<EOF(.*?)\nEOF', text, re.S).group(1)
    assert f"OnCalendar=*-*-* {hh:02d}:{mm:02d}:00 Europe/Moscow" in timer and days is None
    assert not (1 <= hh < 2), "вне окна collect_trudvsem (вторник 01:00)"
    enable = re.search(r"enable --now (.*?)\n(?!\s)", text, re.S).group(1)
    assert "ruware-reembed-night.timer" in enable
    assert load_check.TITLES["reembed_night"] == "Ночной пересчёт векторов"


def test_s10_manual_run_by_day_goes_until_next_0630_msk(conn, conn_factory, base, bge, night, monkeypatch):
    """Ручной запуск днём (12:00 по Москве) не останавливается сразу: срок — 06:30 следующих суток."""
    day = datetime(2026, 10, 25, 9, 0, tzinfo=UTC)   # 12:00 по Москве
    clock = FakeClock(day)
    assert search.night_deadline(conn, day) == datetime(2026, 10, 26, 3, 30, tzinfo=UTC)

    def slow(req):   # каждый запрос к bge-m3 — три часа
        clock.advance(3 * 3600)
        return bge(req)

    monkeypatch.setattr(embed, "client", lambda: httpx.Client(transport=httpx.MockTransport(slow)))
    st = search.reembed_night(conn_factory, clock=clock)
    assert with_vector(conn) == set(base["people"]) and st.changed == 3, "12:00, 15:00, 18:00, 21:00 — всё досчитано"
    assert not st.note, "до срока 06:30 остановки не было"


def test_s10_manual_run_by_day_stops_at_next_0630_msk(conn, conn_factory, base, bge, monkeypatch):
    clock = FakeClock(datetime(2026, 10, 25, 9, 0, tzinfo=UTC))   # 12:00 по Москве

    def slow(req):   # запрос — восемь часов: 12:00 профиль, 20:00 первый, 04:00 второй, в 12:00 следующих суток — стоп
        clock.advance(8 * 3600)
        return bge(req)

    monkeypatch.setattr(embed, "client", lambda: httpx.Client(transport=httpx.MockTransport(slow)))
    st = search.reembed_night(conn_factory, clock=clock)
    assert len(with_vector(conn)) == 2 and "06:30" in (st.note or "")
