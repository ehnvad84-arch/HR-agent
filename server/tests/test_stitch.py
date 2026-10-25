"""T8: сшивка между площадками (S14, S15)."""
from factories import hh_item, profile, source

from hr import db
from hr.normalize import normalize
from hr.pipeline import process

EMPLOYERS = ["Приборный завод «Пример»", "НПП Тест", "ООО Схема"]


def ingest(conn, code, it, clock):
    return process(conn, source(conn, code), normalize(code, it), it, clock.now())


def habr_row(**kw) -> dict:
    row = {"id": "habr-77", "Должность": "Ведущий инженер-электронщик", "Город": "Краснодар",
           "Навыки": "Altium; схемотехника; трассировка; ЭМС", "Места работы": "; ".join(EMPLOYERS),
           "Опыт лет": "9", "Ожидания": "185000"}
    row.update(kw)
    return row


def test_s14_same_person_from_other_source_is_attached(conn, clock):
    profile(conn)
    first = ingest(conn, "hh", hh_item(), clock)
    second = ingest(conn, "habr", habr_row(), clock)
    assert not second.person_created and second.person_id == first.person_id
    assert db.val(conn, "SELECT count(*) FROM person") == 1
    assert db.val(conn, "SELECT count(*) FROM observation WHERE person_id = %s", (first.person_id,)) == 2
    assert [k for k, _ in second.events] == ["resume_updated"]
    assert second.events[0][1]["new_source"] == "habr"


def test_s14_nickname_match_attaches_immediately(conn, clock):
    profile(conn)
    a = ingest(conn, "habr", habr_row(Ник="a_test_dev"), clock)
    other = habr_row(id="manual-5", Город="Сочи", **{"Места работы": "Совсем другая компания", "Ник": "A_Test_Dev"})
    b = ingest(conn, "manual", other, clock)
    assert b.person_id == a.person_id and not b.person_created


def test_s15_doubtful_match_asks_recruiter(conn, clock):
    profile(conn)
    first = ingest(conn, "hh", hh_item(), clock)
    row = habr_row(id="habr-88", **{"Места работы": "Приборный завод «Пример»; Другая фирма"})
    second = ingest(conn, "habr", row, clock)
    assert second.person_created and second.person_id != first.person_id and second.merge_question
    mc = db.one(conn, "SELECT * FROM person_merge_candidate")
    assert {mc["person_a"], mc["person_b"]} == {first.person_id, second.person_id}
    assert 0.6 <= float(mc["similarity"]) < 0.92 and mc["evidence"]["employers"]
    aq = db.one(conn, "SELECT * FROM action_queue WHERE kind = 'merge_persons'")
    assert aq["state"] == "proposed" and aq["payload"]["merge_candidate_id"] == mc["id"]


def test_unrelated_people_are_not_stitched(conn, clock):
    profile(conn)
    ingest(conn, "hh", hh_item(), clock)
    row = habr_row(id="habr-99", Город="Казань", **{"Места работы": "КБ Север; Завод Юг", "Опыт лет": "3"})
    r = ingest(conn, "habr", row, clock)
    assert r.person_created and not r.merge_question
    assert db.val(conn, "SELECT count(*) FROM person_merge_candidate") == 0
