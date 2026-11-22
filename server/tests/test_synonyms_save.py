"""T5: синонимы сохраняются сразу, с журналом и откатом (S2, S3); ключ, удалённый в версии, теряет синонимы."""
from datetime import UTC, datetime, timedelta

import pytest
from factories import profile, user

from hr import db, pipeline, profiles, synonyms, tweaks

NOW = datetime(2026, 11, 22, 11, 5, tzinfo=UTC)


@pytest.fixture
def prof(conn):
    return profile(conn, title="Инженер-электроник", keywords=["Altium", "схемотехника", "ЭМС"])


@pytest.fixture
def rec(conn):
    return user(conn, "rec_syn", "recruiter")


def state(conn, code="engineer_hw_lead"):
    return {r["keyword"]: r["synonyms"] for r in db.rows(
        conn, "SELECT keyword, synonyms FROM role_synonym WHERE role_code = %s ORDER BY keyword", (code,))}


def change(conn, cid):
    return db.one(conn, "SELECT * FROM agent_change WHERE id = %s", (cid,))


def profile_versions(conn, code="engineer_hw_lead"):
    return db.val(conn, "SELECT count(*) FROM role_profile WHERE code = %s", (code,))


def test_s02_save_applies_at_once_without_new_version(conn, prof, rec):
    cid = synonyms.save(conn, prof["id"], [{"keyword": "Altium", "synonyms": ["P-CAD", " KiCad "]},
                                           {"keyword": "Схемотехника", "synonyms": ["разработка схем"]}],
                        rec, NOW, base_change_id=None)
    assert state(conn) == {"altium": ["P-CAD", "KiCad"], "схемотехника": ["разработка схем"]}
    assert profile_versions(conn) == 1, "синонимы не создают версию профиля и не ждут утверждения"
    c = change(conn, cid)
    assert c["kind"] == "synonyms" and c["applied_by"] == rec and c["applied_at"] == NOW
    assert c["before"] == {"role_code": "engineer_hw_lead", "synonyms": {}}
    assert c["after"] == {"role_code": "engineer_hw_lead", "profile_id": prof["id"], "rollback_of": None,
                          "synonyms": {"altium": ["P-CAD", "KiCad"], "схемотехника": ["разработка схем"]}}


def test_s02_second_save_keeps_other_keywords_and_snapshots_before(conn, prof, rec):
    first = synonyms.save(conn, prof["id"], [{"keyword": "Altium", "synonyms": ["P-CAD"]},
                                             {"keyword": "ЭМС", "synonyms": ["электромагнитная совместимость"]}],
                          rec, NOW, base_change_id=None)
    cid = synonyms.save(conn, prof["id"], [{"keyword": "altium", "synonyms": ["KiCad"]},
                                           {"keyword": "ЭМС", "synonyms": []}],
                        rec, NOW + timedelta(minutes=1), base_change_id=first)
    assert state(conn) == {"altium": ["KiCad"]}, "пустой список убирает синонимы ключа"
    c = change(conn, cid)
    assert c["before"]["synonyms"] == {"altium": ["P-CAD"], "эмс": ["электромагнитная совместимость"]}
    assert c["after"]["synonyms"] == {"altium": ["KiCad"]}


@pytest.mark.parametrize("items, why", [
    ([{"keyword": "Altium", "synonyms": ["a" * 61]}], "длина"),
    ([{"keyword": "Altium", "synonyms": ["P-CAD (старый)"]}], "скобки"),
    ([{"keyword": "Altium", "synonyms": ["https://kicad.org"]}], "ссылка"),
    ([{"keyword": "Altium", "synonyms": ["NOT"]}], "оператор hh NOT"),
    ([{"keyword": "Altium", "synonyms": ["P-CAD and KiCad"]}], "оператор hh AND"),
    ([{"keyword": "Altium", "synonyms": ["Or"]}], "оператор hh OR"),
    ([{"keyword": "Altium", "synonyms": ["Alti*"]}], "звёздочка hh"),
    ([{"keyword": "Altium", "synonyms": ["title:Altium"]}], "поле hh"),
    ([{"keyword": "Altium", "synonyms": ["Схемотехника"]}], "совпадает с ключом"),
    ([{"keyword": "Altium", "synonyms": ["altium"]}], "совпадает со своим ключом"),
    ([{"keyword": "Altium", "synonyms": ["KiCad", "kicad"]}], "повтор"),
    ([{"keyword": "Altium", "synonyms": [f"САПР {i}" for i in range(11)]}], "больше 10"),
    ([{"keyword": "Python", "synonyms": ["Django"]}], "ключа нет в версии"),
    ([{"keyword": "Altium", "synonyms": "P-CAD"}], "не список"),
    ([{"keyword": "Altium", "synonyms": ["KiCad"]}, {"keyword": "altium", "synonyms": ["P-CAD"]}], "ключ дважды"),
])
def test_s02_invalid_synonyms_refused(conn, prof, rec, items, why):
    with pytest.raises(synonyms.Invalid):
        synonyms.save(conn, prof["id"], items, rec, NOW, base_change_id=None)
    assert state(conn) == {} and not db.val(conn, "SELECT count(*) FROM agent_change WHERE kind = 'synonyms'"), why


def test_s02_ten_per_keyword_allowed(conn, prof, rec):
    synonyms.save(conn, prof["id"], [{"keyword": "Altium", "synonyms": [f"САПР {i}" for i in range(10)]}],
                  rec, NOW, base_change_id=None)
    assert len(state(conn)["altium"]) == 10


def test_s02_nothing_changed_refused(conn, prof, rec):
    first = synonyms.save(conn, prof["id"], [{"keyword": "Altium", "synonyms": ["P-CAD"]}], rec, NOW,
                          base_change_id=None)
    with pytest.raises(synonyms.Invalid, match="нет изменений"):
        synonyms.save(conn, prof["id"], [{"keyword": "Altium", "synonyms": ["P-CAD"]}], rec, NOW,
                      base_change_id=first)


def test_s02_stale_when_someone_saved_after_opening(conn, prof, rec):
    other = user(conn, "rec_syn2", "recruiter")
    synonyms.save(conn, prof["id"], [{"keyword": "Altium", "synonyms": ["P-CAD"]}], other, NOW, base_change_id=None)
    with pytest.raises(synonyms.Stale):
        synonyms.save(conn, prof["id"], [{"keyword": "Altium", "synonyms": ["KiCad"]}], rec, NOW,
                      base_change_id=None)
    assert state(conn) == {"altium": ["P-CAD"]}


def test_s02_unknown_profile(conn, rec):
    with pytest.raises(synonyms.NotFound):
        synonyms.save(conn, 999999, [{"keyword": "Altium", "synonyms": ["P-CAD"]}], rec, NOW, base_change_id=None)


def test_s02_keywords_of_current_version_even_by_draft_id(conn, prof, rec):
    draft = profile(conn, title="Инженер-электроник", version="1.1", approved=False, keywords=["KiCad"])
    synonyms.save(conn, draft["id"], [{"keyword": "Altium", "synonyms": ["P-CAD"]}], rec, NOW, base_change_id=None)
    with pytest.raises(synonyms.Invalid):
        synonyms.save(conn, draft["id"], [{"keyword": "KiCad", "synonyms": ["OrCAD"]}], rec, NOW,
                      base_change_id=synonyms.last_change_id(conn, "engineer_hw_lead"))


def test_s03_rollback_latest_restores_before(conn, prof, rec):
    first = synonyms.save(conn, prof["id"], [{"keyword": "Altium", "synonyms": ["P-CAD"]}], rec, NOW,
                          base_change_id=None)
    second = synonyms.save(conn, prof["id"], [{"keyword": "Altium", "synonyms": ["KiCad", "OrCAD"]}], rec, NOW,
                           base_change_id=first)
    cid = synonyms.rollback(conn, prof["id"], second, rec, NOW + timedelta(minutes=2))
    assert state(conn) == {"altium": ["P-CAD"]}
    c = change(conn, cid)
    assert c["kind"] == "synonyms" and c["applied_by"] == rec
    assert c["before"]["synonyms"] == {"altium": ["KiCad", "OrCAD"]}
    assert c["after"] == {"role_code": "engineer_hw_lead", "profile_id": prof["id"], "rollback_of": second,
                          "synonyms": {"altium": ["P-CAD"]}}
    assert synonyms.last_change_id(conn, "engineer_hw_lead") == cid, "откат тоже в журнале"


def test_s03_rollback_of_first_save_clears(conn, prof, rec):
    first = synonyms.save(conn, prof["id"], [{"keyword": "Altium", "synonyms": ["P-CAD"]}], rec, NOW,
                          base_change_id=None)
    synonyms.rollback(conn, prof["id"], first, rec, NOW)
    assert state(conn) == {}


def test_s03_rollback_not_latest_is_conflict(conn, prof, rec):
    first = synonyms.save(conn, prof["id"], [{"keyword": "Altium", "synonyms": ["P-CAD"]}], rec, NOW,
                          base_change_id=None)
    synonyms.save(conn, prof["id"], [{"keyword": "Altium", "synonyms": ["KiCad"]}], rec, NOW, base_change_id=first)
    with pytest.raises(synonyms.NotLatest):
        synonyms.rollback(conn, prof["id"], first, rec, NOW)
    assert state(conn) == {"altium": ["KiCad"]}


def test_s03_rollback_of_other_role_or_kind_not_found(conn, prof, rec):
    other = profile(conn, code="qc_engineer", title="Инженер ОТК", dept="qc", keywords=["ОТК"])
    foreign = synonyms.save(conn, other["id"], [{"keyword": "ОТК", "synonyms": ["технический контроль"]}], rec,
                            NOW, base_change_id=None)
    with pytest.raises(synonyms.NotFound):
        synonyms.rollback(conn, prof["id"], foreign, rec, NOW)
    weight = db.val(conn, "INSERT INTO agent_change (kind, before, after) VALUES ('weight', '{}', '{}') RETURNING id")
    with pytest.raises(synonyms.NotFound):
        synonyms.rollback(conn, prof["id"], weight, rec, NOW)


def test_s02_keyword_removed_in_approved_version_loses_synonyms(conn, prof, rec):
    synonyms.save(conn, prof["id"], [{"keyword": "Altium", "synonyms": ["P-CAD"]},
                                     {"keyword": "ЭМС", "synonyms": ["электромагнитная совместимость"]}],
                  rec, NOW, base_change_id=None)
    head = user(conn, "head_syn", "head_recruiter")
    new = profiles.edit(conn, prof["id"], {"keywords": ["Altium", "схемотехника"]}, rec, NOW)
    assert state(conn) == {"altium": ["P-CAD"], "эмс": ["электромагнитная совместимость"]}, \
        "до утверждения синонимы на месте"
    profiles.approve(conn, new["id"], head, NOW + timedelta(hours=1))
    assert state(conn) == {"altium": ["P-CAD"]}
    c = change(conn, synonyms.last_change_id(conn, "engineer_hw_lead"))
    assert c["kind"] == "synonyms" and c["applied_by"] == head
    assert c["before"]["synonyms"] == {"altium": ["P-CAD"], "эмс": ["электромагнитная совместимость"]}
    assert c["after"]["synonyms"] == {"altium": ["P-CAD"]}
    assert "ключ удалён в версии " + new["version"] in c["note"]


def test_s02_approve_keeping_keywords_writes_nothing(conn, prof, rec):
    first = synonyms.save(conn, prof["id"], [{"keyword": "Altium", "synonyms": ["P-CAD"]}], rec, NOW,
                          base_change_id=None)
    new = profiles.edit(conn, prof["id"], {"title": "Инженер-электроник (ведущий)"}, rec, NOW)
    profiles.approve(conn, new["id"], user(conn, "head_syn", "head_recruiter"), NOW)
    assert state(conn) == {"altium": ["P-CAD"]} and synonyms.last_change_id(conn, "engineer_hw_lead") == first


def test_s02_background_rescore_of_current_version(conn, conn_factory, prof, rec, monkeypatch):
    calls = []
    monkeypatch.setattr(pipeline, "rescore_profile", lambda cf, pid, **_: calls.append(("profile", pid)))
    monkeypatch.setattr(tweaks, "user_rescore", lambda cf, **_: calls.append(("user", None)))
    draft = profile(conn, title="Инженер-электроник", version="1.1", approved=False)
    synonyms.rescore(conn_factory, draft["id"])
    assert calls == [("profile", prof["id"]), ("user", None)], "пересчёт по действующей версии и «мой балл»"
