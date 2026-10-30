"""Сбор с «Работы России»: еженедельно, только регион и подходящие под портреты (S45)."""
import json

import httpx
from factories import profile

from hr import collect, db, tasks
from hr.clock import FakeClock

HDR = ("id|candidateId|stateRegionCode|locality|localityName|birthday|gender|age|positionName|dateCreate|"
       "typicalPosition|dateModify|publishedDate|academicDegree|skills|experience|workExperienceList|salary|"
       "salaryMin|salaryMax|relocation|hardSkills|educationList")


def row(rid, region="2300000000000", title="Ведущий-инженер-электронщик", modified="2026-10-01T10:00:00+0300",
        skills=("Altium Designer", "схемотехника", "трассировка", "ЭМС"), job="Инженер-электронщик"):
    work = [{"companyName": "Завод Пример", "jobTitle": job, "dateFrom": "2017-01-01T00:00:00+0300",
             "dateTo": None, "type": "WorkExperience"}]
    hard = [{"hardSkillName": s} for s in skills]
    cells = [rid, "cand-" + rid, region, region, "Краснодарский-край,-г-Краснодар", "1980-01-01T00:00:00+0300",
             "Мужской", "46", title, "2024-01-01T00:00:00+0300", "", modified, modified, "", "[]", "9",
             json.dumps(work, ensure_ascii=False), "0", "180000", "0", "Не-готов-к-переезду",
             json.dumps(hard, ensure_ascii=False), "[]"]
    return "|".join(cells)


class FakeTrud:
    def __init__(self, lines):
        self.body = ("\n".join([HDR, *lines]) + "\n").encode()
        self.calls = 0

    def __call__(self, req):
        self.calls += 1
        assert req.url.path == "/csv/cv.csv"
        return httpx.Response(200, content=self.body)

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self))


def test_s45_only_region_fresh_and_fitting(conn, conn_factory):
    profile(conn)
    db.run(conn, "UPDATE source SET is_active = true WHERE code = 'trudvsem'")
    clock = FakeClock()
    fake = FakeTrud([row("fit"), row("moscow", region="7700000000000"),
                     row("old", modified="2023-01-01T10:00:00+0300"),
                     row("seller", title="Продавец-кассир", skills=("касса",), job="Продавец")])
    st = collect.collect_trudvsem(conn_factory, http=fake.client(), clock=clock)
    obs = db.rows(conn, """SELECT o.external_id, o.title, o.city, o.raw FROM observation o
                           JOIN source s ON s.id = o.source_id WHERE s.code = 'trudvsem'""")
    assert [o["external_id"] for o in obs] == ["fit"]
    o = obs[0]
    assert o["title"] == "Ведущий инженер электронщик" and "Краснодар" in o["city"]
    assert not {"birthday", "gender", "age"} & set(o["raw"]), "личное без надобности не храним"
    assert st.seen == 3 and st.skipped >= 2 and st.new == 1
    assert "collect_trudvsem" in tasks.TASKS


def test_inactive_source_not_collected(conn, conn_factory):
    profile(conn)
    db.run(conn, "UPDATE source SET is_active = false WHERE code = 'trudvsem'")
    import pytest

    from hr.jobs import JobNotConfigured
    with pytest.raises(JobNotConfigured):
        collect.collect_trudvsem(conn_factory, http=FakeTrud([]).client(), clock=FakeClock())
