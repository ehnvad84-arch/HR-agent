"""T2 manager-cabinet: граница видимости по оргструктуре (S5, S17, S18)."""
from factories import org, user
from fastapi import Depends, FastAPI

from hr import auth, db, scope


class U:
    def __init__(self, uid, role, perms=()):
        self.id, self.role, self.permissions = uid, role, frozenset(perms)


def test_subtree_codes_and_vacancies(conn, clock):
    o = org(conn, clock.now())
    u = o["units"]
    head_sales = user(conn, "sales_head", "hiring_manager", u["u-sales"])
    head_s1 = user(conn, "s1_head", "hiring_manager", u["u-s1"])
    head_rnd = user(conn, "rnd_head", "hiring_manager", u["u-rnd"])
    vac = db.val(conn, """INSERT INTO vacancy (role_profile_id, title, staff_position_id) VALUES (%s, 'МПП', %s)
                          RETURNING id""", (o["sales"]["id"], o["pos"]["p-mpp1"]))
    s = scope.for_user(conn, U(head_sales, "hiring_manager"))
    assert s.units == {u["u-sales"], u["u-s1"], u["u-s2"]} and s.codes == {"sales_project"} and vac in s.vacancies
    s1 = scope.for_user(conn, U(head_s1, "hiring_manager"))
    assert s1.units == {u["u-s1"]} and vac in s1.vacancies
    rnd = scope.for_user(conn, U(head_rnd, "hiring_manager"))
    assert rnd.codes == {"engineer_hw_lead"} and vac not in rnd.vacancies and not rnd.code_ok("sales_project")


def test_director_hr_and_no_unit(conn, clock):
    o = org(conn, clock.now())
    d = scope.for_user(conn, U(user(conn, "dir", "director"), "director"))
    assert not d.all and set(o["units"].values()) == d.units and {"sales_project", "engineer_hw_lead"} <= d.codes
    assert scope.for_user(conn, U(1, "recruiter", {"base.all"})).all
    lonely = scope.for_user(conn, U(user(conn, "lonely", "hiring_manager"), "hiring_manager"))
    assert lonely.reason == "no_unit" and not lonely.units and lonely.public()["reason"] == "no_unit"


def test_person_visible_by_match(conn, clock):
    o = org(conn, clock.now())
    pid = db.val(conn, "INSERT INTO person (label) VALUES ('Инженер, 8 лет') RETURNING id")
    db.run(conn, """INSERT INTO match (person_id, role_profile_id, profile_version, score) VALUES (%s, %s, %s, 70)""",
           (pid, o["eng"]["id"], o["eng"]["version"]))
    rnd = scope.for_user(conn, U(user(conn, "rnd_head", "hiring_manager", o["units"]["u-rnd"]), "hiring_manager"))
    sales = scope.for_user(conn, U(user(conn, "sales_head", "hiring_manager", o["units"]["u-sales"]),
                                   "hiring_manager"))
    assert scope.person_ok(conn, rnd, pid) and not scope.person_ok(conn, sales, pid)
    assert scope.person_ok(conn, scope.ALL, pid)


def test_guard_catches_unscoped_route():
    app = FastAPI()
    plain, scoped = Depends(auth.require("base.read")), Depends(auth.require_scoped("base.read"))

    @app.get("/api/leak")
    def leak(u=plain):
        return {}

    @app.get("/api/ok")
    def ok(us=scoped):
        return {}

    assert auth.routes_unscoped(app) == ["GET /api/leak"]


def test_me_has_scope(conn, api):
    c, _, _ = api("recruiter")
    assert c.get("/api/me").json()["scope"] == {"all": True, "units": [], "reason": None, "unit_title": None}
    m, _, _ = api("hiring_manager")
    assert m.get("/api/me").json()["scope"]["reason"] == "no_unit"
