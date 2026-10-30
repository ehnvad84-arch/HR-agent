"""Оргструктура из 1С (S47): дерево подразделений, ставки, портреты-заготовки; сотрудники не хранятся."""
import copy
import json

from factories import profile

from hr import config, db, org_1c

FAKE_PERSON = "Тестовый Сотрудник Вымышленович"


def report() -> dict:
    """Вымышленный отчёт в формате 1С «Организационная структура»."""
    emp = [{"name": FAKE_PERSON, "personnelNumber": "ТТ-00001", "birthDate": "1990-01-01", "age": 36}]
    return {
        "report": "Отчет.ОрганизационнаяСтруктура", "period": "2026-10-30", "organization": "Тестовая организация",
        "departments": [{
            "id": "root-0001", "name": "Основное подразделение", "order": 96, "parentId": "",
            "positions": [{"id": "p-dir", "name": "Директор /Основное/", "order": 14, "rates": 1, "occupied": 0.25,
                           "vacant": 0.75, "employees": emp}],
            "departments": [{
                "id": "sales-0001", "name": "Департамент продаж", "order": 2, "parentId": "root-0001",
                "positions": [],
                "departments": [
                    {"id": "sales-0003", "name": "Отдел продаж №3", "order": 53, "parentId": "sales-0001",
                     "positions": [{"id": "p-mpp3", "name": "Менеджер проектных продаж /Отдел продаж/", "order": 55,
                                    "rates": 7, "occupied": 5, "vacant": 2, "employees": emp * 5}],
                     "departments": []},
                    {"id": "sales-0005", "name": "Отдел продаж №5 ", "order": 52, "parentId": "sales-0001",
                     "positions": [{"id": "p-mpp5", "name": "Менеджер проектных продаж /Отдел продаж/", "order": 55,
                                    "rates": 7, "occupied": 0, "vacant": 7, "employees": []},
                                   {"id": "p-rop5", "name": "Руководитель отдела проектных продаж /Отдел продаж/",
                                    "order": 347, "rates": 1, "occupied": 2, "vacant": -1, "employees": emp * 2}],
                     "departments": []}]}]}]}


def raw(r: dict) -> bytes:
    return json.dumps(r, ensure_ascii=False).encode()


def test_s47_apply_builds_tree_without_employees(conn, clock):
    sales = profile(conn, "sales_project", "Менеджер проектных продаж", dept="sales")
    res = org_1c.apply(conn, raw(report()), "org.json", None, clock.now())
    assert res == {"state": "applied", "file_name": "org.json", "units": 4, "positions": 4, "changes": 8,
                   "new_profiles": 2}
    units = {u["title"]: u for u in db.rows(conn, "SELECT * FROM org_unit")}
    assert units["Отдел продаж №5"]["parent_id"] == units["Департамент продаж"]["id"]
    assert units["Департамент продаж"]["parent_id"] == units["Основное подразделение"]["id"]
    pos = {p["external_id"]: p for p in db.rows(conn, "SELECT * FROM staff_position")}
    assert float(pos["p-mpp3"]["rates_open"]) == 2 and float(pos["p-rop5"]["rates_open"]) == 0
    # ни ФИО, ни табельных номеров, ни дат рождения — нигде
    dump = json.dumps([db.rows(conn, f"SELECT * FROM {t}") for t in
                       ("org_unit", "staff_position", "org_change", "job_role_map", "role_profile", "org_sync")],
                      default=str, ensure_ascii=False)
    assert FAKE_PERSON not in dump and "ТТ-00001" not in dump and "1990-01-01" not in dump
    # одноимённый портрет связан, остальные — пустые заготовки
    m = {r["job_title"]: r["role_code"] for r in db.rows(conn, "SELECT * FROM job_role_map")}
    assert m["Менеджер проектных продаж"] == sales["code"]
    assert m["Директор"].startswith("1c-") and m["Руководитель отдела проектных продаж"].startswith("1c-")
    assert db.val(conn, "SELECT count(*) FROM agent_change WHERE note LIKE 'из оргструктуры 1С%%'") == 1


def test_s47_same_file_twice_is_noop(conn, clock):
    org_1c.apply(conn, raw(report()), "org.json", None, clock.now())
    assert org_1c.apply(conn, raw(report()), "org.json", None, clock.now())["state"] == "same"
    assert db.val(conn, "SELECT count(*) FROM org_sync") == 1


def test_s47_changes_are_recorded(conn, clock):
    org_1c.apply(conn, raw(report()), "org.json", None, clock.now())
    db.run(conn, "UPDATE settings SET value = '50' WHERE key = 'org_max_drop_pct'")
    r = copy.deepcopy(report())
    sales = r["departments"][0]["departments"][0]
    sales["departments"][0]["name"] = "Отдел продаж №3 (Юг)"
    sales["departments"][0]["positions"][0]["occupied"] = 6
    sales["departments"][1]["positions"].pop()   # руководителя убрали из штата
    res = org_1c.apply(conn, raw(r), "org2.json", None, clock.now())
    kinds = sorted(x["kind"] for x in db.rows(conn, "SELECT kind::text FROM org_change WHERE sync_id = "
                                                    "(SELECT max(id) FROM org_sync)"))
    assert kinds == ["occupancy_changed", "position_closed", "unit_renamed"] and res["new_profiles"] == 0
    assert db.val(conn, "SELECT is_active FROM staff_position WHERE external_id = 'p-rop5'") is False


def test_s47_too_many_missing_is_rejected(conn, clock):
    org_1c.apply(conn, raw(report()), "org.json", None, clock.now())
    r = report()
    r["departments"][0]["departments"] = []
    try:
        org_1c.apply(conn, raw(r), "bad.json", None, clock.now())
    except org_1c.Invalid as e:
        assert "пропало 3 из 4" in str(e)
    else:
        raise AssertionError("неполная выгрузка применилась")


def test_s47_not_a_report(conn, clock):
    for bad in (b"not json", b'{"units": []}'):
        try:
            org_1c.apply(conn, bad, "x.json", None, clock.now())
        except org_1c.Invalid:
            pass
        else:
            raise AssertionError(bad)


def test_s47_api_refresh_tree_and_empty_profile(conn, api, tmp_path, monkeypatch):
    profile(conn, "sales_project", "Менеджер проектных продаж", dept="sales")
    monkeypatch.setenv("EXCHANGE_1C_DIR", str(tmp_path))
    config.get_config.cache_clear()
    c, h, _ = api("head_recruiter")
    assert c.post("/api/org/refresh", headers=h).status_code == 404
    (tmp_path / "orgstructure.json").write_bytes(raw(report()))
    r = c.post("/api/org/refresh", headers=h)
    assert r.status_code == 200 and r.json()["new_profiles"] == 2
    body = c.get("/api/org").json()
    assert body["sync"]["file_name"] == "orgstructure.json"
    units = {u["title"]: u for u in body["units"]}
    mpp = units["Отдел продаж №3"]["positions"][0]
    assert mpp["title"] == "Менеджер проектных продаж" and mpp["rates_open"] == 2
    assert mpp["profile"]["state"] == "approved"
    director = units["Основное подразделение"]["positions"][0]
    assert director["profile"]["state"] == "empty"
    assert FAKE_PERSON not in json.dumps(body, ensure_ascii=False)
    # пустой портрет утвердить нельзя
    r = c.post(f"/api/profile/{director['profile']['id']}/approve", headers=h)
    assert r.status_code == 422 and "заполните критерии" in r.json()["message"]
    states = {p["title"]: p["state"] for p in c.get("/api/profiles").json()["items"]}
    assert states["Директор"] == "empty"
    # рекрутер видит дерево, но обновлять не может
    rc, rh, _ = api("recruiter")
    assert rc.get("/api/org").status_code == 200
    assert rc.post("/api/org/refresh", headers=rh).status_code == 403


def test_s47_api_upload_rejects_garbage(conn, api):
    c, h, _ = api("head_recruiter")
    r = c.post("/api/org/upload", headers=h, files={"file": ("x.json", b"[]", "application/json")})
    assert r.status_code == 422 and "не применена" in r.json()["message"]
    assert db.val(conn, "SELECT state FROM org_sync") == "rejected"
    r = c.post("/api/org/upload", headers=h, files={"file": ("org.json", raw(report()), "application/json")})
    assert r.status_code == 200 and r.json()["units"] == 4
