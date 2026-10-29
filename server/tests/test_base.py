"""T13 workplace: база с фильтрами и сортировкой, поиск по смыслу, подходящие к вакансии (S19, S20, S22)."""
import pytest
from factories import hh_item, profile, source

from hr import db, search
from hr.normalize import normalize
from hr.pipeline import process

LONG = "Не подходит по опыту: " + "много лет в рознице, профильной электроники нет, ожидания выше вилки. " * 4


def add(conn, clock, rid, city="Краснодар", skills=None, title="Ведущий инженер-электронщик", employer=None):
    exp = [{"company": employer or f"Завод {rid}", "position": title, "start": "2017-01-01", "end": None}]
    it = hh_item(id=rid, title=title, area={"id": "53", "name": city}, experience=exp,
                 skill_set=skills or ["Altium Designer", "схемотехника", "трассировка", "ЭМС"])
    return process(conn, source(conn, "hh"), normalize("hh", it), it, clock.now()).person_id


@pytest.fixture
def base(conn, clock):
    prof = profile(conn)
    a = add(conn, clock, "a", city="Краснодар")
    b = add(conn, clock, "b", city="Армавир", skills=["Altium Designer", "схемотехника"])
    return prof, a, b


def test_s19_base_lists_people_with_columns(conn, api, base):
    _, a, b = base
    c, _, _ = api()
    r = c.get("/api/base").json()
    assert r["total"] == 2
    row = next(x for x in r["items"] if x["person_id"] == a)
    assert {"person_id", "name", "label", "city", "role_title", "dept", "score", "where", "sources", "socn",
            "updated"} <= set(row)
    assert row["sources"] == ["hh"] and row["where"] == "observed"


def test_s19_sort_and_filter(conn, api, base):
    _, a, b = base
    c, _, _ = api()
    by_score = c.get("/api/base", params={"sort": "score", "dir": "desc"}).json()["items"]
    assert by_score[0]["score"] >= by_score[1]["score"]
    by_city = c.get("/api/base", params={"sort": "city", "dir": "asc"}).json()["items"]
    assert [x["city"] for x in by_city] == ["Армавир", "Краснодар"]
    only = c.get("/api/base", params={"city": "Армавир"}).json()
    assert only["total"] == 1 and only["items"][0]["person_id"] == b
    hi = c.get("/api/base", params={"score_min": by_score[0]["score"]}).json()
    assert all(x["score"] >= by_score[0]["score"] for x in hi["items"])
    assert c.get("/api/base", params={"source": "habr"}).json()["total"] == 0


def test_unknown_sort_column_422(api, base):
    c, _, _ = api()
    assert c.get("/api/base", params={"sort": "id; DROP TABLE person"}).status_code == 422
    assert c.get("/api/base", params={"dir": "sideways"}).status_code == 422


def test_paging(conn, api, base):
    db.run(conn, "UPDATE settings SET value = '1' WHERE key = 'base_page_size'")
    c, _, _ = api()
    p0 = c.get("/api/base", params={"page": 0}).json()
    p1 = c.get("/api/base", params={"page": 1}).json()
    assert p0["total"] == 2 and len(p0["items"]) == 1 and p0["items"][0]["person_id"] != p1["items"][0]["person_id"]


def test_s22_matching_by_vacancy_desc_without_rejected(conn, api, base):
    prof, a, b = base
    vid = db.val(conn, "INSERT INTO vacancy (role_profile_id, title) VALUES (%s, 'В') RETURNING id", (prof["id"],))
    c, h, uid = api()
    r = c.get(f"/api/vacancy/{vid}/matching").json()
    scores = [x["score"] for x in r["items"]]
    assert scores == sorted(scores, reverse=True) and len(scores) == 2
    c.post("/api/decision", json={"person_id": b, "kind": "reject", "role_profile_id": prof["id"], "comment": LONG},
           headers=h)
    assert [x["person_id"] for x in c.get(f"/api/vacancy/{vid}/matching").json()["items"]] == [a]
    assert c.get(f"/api/vacancy/{vid}/candidates").status_code == 200
    assert c.get("/api/vacancy/999999/matching").status_code == 404


def test_s20_semantic_search(conn, conn_factory, api, base):
    search.reembed(conn_factory)
    c, _, _ = api()
    r = c.get("/api/search", params={"q": "схемотехник Altium"})
    assert r.status_code == 200 and r.json()["items"] and "similarity" in r.json()["items"][0]
    assert "label" in r.json()["items"][0]
    assert c.get("/api/search", params={"q": " "}).status_code == 422

