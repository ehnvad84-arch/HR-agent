"""T6: наблюдение, версии, дифф, события (S2, S9, S10)."""
import copy
import json
from pathlib import Path

import pytest

from hr import db
from hr.normalize import normalize
from hr.pipeline import events_from_diff, upsert_observation

FIX = Path(__file__).parent / "fixtures"


def item() -> dict:
    return json.loads((FIX / "hh" / "search_item.json").read_text(encoding="utf-8"))


@pytest.fixture
def hh(conn):
    return db.one(conn, "SELECT * FROM source WHERE code = 'hh'")


def put(conn, src, it, clock):
    return upsert_observation(conn, src, normalize(src["code"], it), it, clock.now())


def test_s02_repeat_does_not_duplicate_and_updates_last_seen(conn, hh, clock):
    first = put(conn, hh, item(), clock)
    assert first.new
    clock.advance(days=1)
    again = put(conn, hh, item(), clock)
    assert not again.new and not again.changed and again.id == first.id
    assert db.val(conn, "SELECT count(*) FROM observation") == 1
    assert db.val(conn, "SELECT count(*) FROM obs_version") == 1
    seen = db.one(conn, "SELECT first_seen, last_seen FROM observation WHERE id = %s", (first.id,))
    assert (seen["last_seen"] - seen["first_seen"]).days == 1


def test_s09_raw_stored_as_is(conn, hh, clock):
    it = item()
    r = put(conn, hh, it, clock)
    raw = db.val(conn, "SELECT raw FROM observation WHERE id = %s", (r.id,))
    ver = db.val(conn, "SELECT raw FROM obs_version WHERE observation_id = %s", (r.id,))
    assert raw == it and ver == it
    assert raw["first_name"] == "Андрей" and raw["contact"]


CASES = [
    ("employer_changed", lambda it: it["experience"].insert(0, {"company": "Новый завод", "position": "Инженер",
                                                                 "start": "2026-09-01", "end": None})),
    ("salary_changed", lambda it: it["salary"].update(amount=210000)),
    ("opened_for_search", None),
    ("skill_added", lambda it: it.update(skill_set=["Altium"])),
    ("geo_changed", lambda it: it.update(area={"id": "76", "name": "Ростов-на-Дону"})),
    ("resume_updated", lambda it: it.update(updated_at="2026-10-25T09:00:00+0300")),
]


@pytest.mark.parametrize("kind,change", CASES, ids=[c[0] for c in CASES])
def test_s10_change_gives_event_kind(conn, hh, clock, kind, change):
    base = item()
    if kind == "opened_for_search":
        base["job_search_status"] = {"id": "not_looking_for_job"}
    put(conn, hh, base, clock)
    new = copy.deepcopy(base)
    if kind == "opened_for_search":
        new["job_search_status"] = {"id": "active_search"}
    else:
        change(new)
    clock.advance(days=1)
    r = put(conn, hh, new, clock)
    assert r.changed
    assert db.val(conn, "SELECT count(*) FROM obs_version WHERE observation_id = %s", (r.id,)) == 2
    assert db.val(conn, "SELECT diff FROM obs_version WHERE observation_id = %s ORDER BY id DESC LIMIT 1", (r.id,))
    kinds = [k for k, _ in events_from_diff(r.diff, keywords={"altium", "схемотехника"})]
    assert kind in kinds
    if kind != "resume_updated":
        assert "resume_updated" not in kinds, "общее событие — только если других нет"


def test_skill_not_in_keywords_is_plain_update(conn, hh, clock):
    put(conn, hh, item(), clock)
    new = item()
    new["skill_set"] = ["Вышивание крестиком"]
    r = put(conn, hh, new, clock)
    assert [k for k, _ in events_from_diff(r.diff, keywords={"altium"})] == ["resume_updated"]
