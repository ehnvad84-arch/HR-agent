"""T8 funnel-report: счёт воронки по отметкам (S11–S13, S16) — 8 строк «вошли · прошли · %», конверсия, срок ведения
без пауз, воронка рекрутера за год и отдела. Люди и вакансии вымышлены; цифры эталона посчитаны руками."""
import random
from datetime import UTC, date, datetime

import pytest
from factories import profile, user

from hr import db, funnel_report, stages, vacancies

NOW = datetime(2026, 11, 23, 9, 0, tzinfo=UTC)
TODAY = date(2026, 11, 23)
ROWS = ["selected", "contact", "screening", "interview", "meeting", "security", "offer", "first_day"]
OTHER_100 = "Кандидат попросил отложить выход на два месяца из-за переезда семьи, ждать вакансия не может, " \
            "решили завершить сейчас."


@pytest.fixture
def people(conn):
    return {"rec": user(conn, "rec", "recruiter"), "rec2": user(conn, "rec2", "recruiter"),
            "head": user(conn, "head", "head_recruiter"), "boss": user(conn, "boss", "hiring_manager")}


@pytest.fixture
def prof(conn):
    return profile(conn)


def new_vacancy(conn, prof, people, title="Ведущий инженер-электронщик", recruiter="rec", opened=date(2026, 11, 3)):
    vid = vacancies.create(conn, prof["id"], title, people["rec"], recruiter_id=people[recruiter],
                           hiring_manager_id=people["boss"])
    db.run(conn, "UPDATE vacancy SET opened_at = %s WHERE id = %s", (opened, vid))
    return vid


def sid(conn, title):
    return db.val(conn, "SELECT id FROM ops.stage WHERE title = %s", (title,))


def candidate(conn):
    return db.val(conn, """INSERT INTO candidate (fio, retention_until) VALUES ('Вымышленный В. В.', current_date + 100)
                           RETURNING id""")


def open_slot(conn, cid, vid):
    return db.val(conn, """SELECT slot FROM ops.candidate_stage WHERE candidate_id = %s AND vacancy_id = %s
                           AND outcome IS NULL AND cancelled_at IS NULL""", (cid, vid))


def path(conn, vid, user_id, steps, end=None):
    """Кандидат входит в воронку, steps раз «прошёл»; end — (исход, причина) открытого этапа или None."""
    cid = candidate(conn)
    stages.enter(conn, cid, vid, user_id, NOW)
    for _ in range(steps):
        stages.mark(conn, cid, vid, open_slot(conn, cid, vid), "passed", user_id, NOW)
    if end:
        outcome, reason = end
        stages.mark(conn, cid, vid, open_slot(conn, cid, vid), outcome, user_id, NOW, reason=reason,
                    comment=OTHER_100 if reason == "other" else None)
    return cid


def four_meetings_without_security(conn, vid, people):
    """Набор эталона: 4 собеседования (стандартное + три с руководителями), без «Проверки СБ»; утверждён."""
    items = [{"slot": i["slot"], "stage_id": i["stage_id"], "label": i["label"]}
             for i in stages.current_set(conn, vid)["items"] if i["report_row"] != "security"]
    meet = sid(conn, "Собеседование с руководителем")
    at = next(n for n, i in enumerate(items) if i["slot"] == 5) + 1
    extra = [{"stage_id": meet, "label": f"Собеседование: {who}"} for who in ("главный инженер", "директор", "ОТК")]
    res = stages.propose(conn, vid, items[:at] + extra + items[at:], "четыре собеседования", people["rec"], NOW)
    stages.approve(conn, res["id"], people["head"], NOW)


def table(report):
    return {r["code"]: (r["entered"], r["passed"], r["pct"]) for r in report["rows"]}


def counts(report):
    return [(r["entered"], r["passed"]) for r in report["rows"]]


# ------------------------------------------------------------------ S11: эталон

# Набор (позиции): Отобранные · Контакт · Первичное интервью · Интервью · Собеседование ×4 · Оффер · Пришедшие в 1 день.
# Кандидаты: A вышел; B не вышел в первый день; C отказ от оффера; D не прошёл 3-е собеседование; E на 1-м
# собеседовании без исхода; F не прошёл тестовое на «Интервью»; G — условия на первичном интервью; H — не вышел на
# связь; I — не прошёл отбор резюме; J — на «Отобранных» без исхода.
REFERENCE = {
    "selected": (10, 8, 80), "contact": (8, 7, 88), "screening": (7, 6, 86), "interview": (6, 5, 83),
    "meeting": (5, 3, 60), "security": (3, 3, 100), "offer": (3, 2, 67), "first_day": (2, 1, 50),
}
REFERENCE_MEETINGS = [("Собеседование", 5, 4), ("Собеседование: главный инженер", 4, 4),
                      ("Собеседование: директор", 4, 3), ("Собеседование: ОТК", 3, 3)]


def reference_vacancy(conn, prof, people):
    vid = new_vacancy(conn, prof, people)
    four_meetings_without_security(conn, vid, people)
    rec = people["rec"]
    path(conn, vid, rec, 10)                                     # A
    path(conn, vid, rec, 9, ("refused", "no_first_day"))         # B
    path(conn, vid, rec, 8, ("refused", "offer_declined"))       # C
    path(conn, vid, rec, 6, ("failed", "skills"))                # D
    path(conn, vid, rec, 4)                                      # E
    path(conn, vid, rec, 3, ("failed", "test_task"))             # F
    path(conn, vid, rec, 2, ("refused", "conditions"))           # G
    path(conn, vid, rec, 1, ("refused", "no_contact"))           # H
    path(conn, vid, rec, 0, ("failed", "skills"))                # I
    path(conn, vid, rec, 0)                                      # J
    vacancies.pause(conn, vid, rec, datetime(2026, 11, 10, 9, 0, tzinfo=UTC))
    vacancies.resume(conn, vid, rec, datetime(2026, 11, 16, 9, 0, tzinfo=UTC))
    return vid


def test_s11_reference_vacancy_four_meetings_without_security(conn, prof, people):
    vid = reference_vacancy(conn, prof, people)
    rep = funnel_report.vacancy_report(conn, vid, today=TODAY)
    assert [r["code"] for r in rep["rows"]] == ROWS
    assert table(rep) == REFERENCE
    by = {r["code"]: r for r in rep["rows"]}
    assert by["security"]["in_set"] is False and by["security"]["stages"] == []
    assert all(r["in_set"] for c, r in by.items() if c != "security")
    assert [(s["title"], s["entered"], s["passed"]) for s in by["meeting"]["stages"]] == REFERENCE_MEETINGS
    assert [(s["entered"], s["passed"]) for s in by["offer"]["stages"]] == [(3, 2)]
    assert rep["conversion"] == {"hired": 1, "selected": 10, "pct": 10}
    assert rep["title"] == "Ведущий инженер-электронщик"
    assert (rep["opened_at"], rep["closed_at"]) == (date(2026, 11, 3), None)
    assert (rep["days"], rep["paused_days"]) == (14, 6)               # 20 дней с открытия, 6 на паузе
    assert rep["carried"] == 0
    assert rep["since"] == db.val(conn, """SELECT (applied_at AT TIME ZONE 'Europe/Moscow')::date
                                          FROM ops.schema_migration WHERE name = '012_funnel_stages.sql'""")


def test_s11_empty_vacancy_zeros_and_dash(conn, prof, people):
    vid = new_vacancy(conn, prof, people)
    rep = funnel_report.vacancy_report(conn, vid, today=TODAY)
    assert counts(rep) == [(0, 0)] * 8
    assert all(r["pct"] is None and r["in_set"] for r in rep["rows"])
    assert rep["conversion"] == {"hired": 0, "selected": 0, "pct": None}
    with pytest.raises(funnel_report.NotFound):
        funnel_report.vacancy_report(conn, 999999, today=TODAY)


def test_s11_term_without_pauses_and_closed_gap(conn, prof, people):
    vid = new_vacancy(conn, prof, people, opened=date(2026, 9, 1))
    db.run(conn, """INSERT INTO ops.vacancy_pause (vacancy_id, from_date, to_date, kind) VALUES
                    (%s, '2026-09-10', '2026-09-15', 'pause'), (%s, '2026-10-01', '2026-10-11', 'closed_gap')""",
           (vid, vid))
    db.run(conn, "INSERT INTO ops.vacancy_pause (vacancy_id, from_date, kind) VALUES (%s, '2026-11-20', 'pause')",
           (vid,))
    rep = funnel_report.vacancy_report(conn, vid, today=TODAY)
    # 01.09 → 23.11 = 83 дня; паузы: 5 + 10 + открытая с 20.11 — 3
    assert (rep["days"], rep["paused_days"]) == (65, 18)
    vacancies.close(conn, vid, OTHER_100 * 2, people["rec"], datetime(2026, 11, 21, 9, 0, tzinfo=UTC))
    rep = funnel_report.vacancy_report(conn, vid, today=TODAY)
    assert rep["closed_at"] == date(2026, 11, 21)
    assert (rep["days"], rep["paused_days"]) == (65, 16)                 # 81 день до закрытия, паузы 5 + 10 + 1


# ------------------------------------------------------------------ свойства счёта

def test_s11_entered_next_equals_passed_on_random_data(conn, prof, people):
    rnd = random.Random(20261123)
    for n in range(3):
        vid = new_vacancy(conn, prof, people, title=f"Вакансия {n}")
        if n:                                   # вторая — без СБ и 4 собеседования, третья — без «Интервью»
            items = [{"slot": i["slot"], "stage_id": i["stage_id"], "label": i["label"]}
                     for i in stages.current_set(conn, vid)["items"]
                     if i["report_row"] != ("security" if n == 1 else "interview")]
            if n == 1:
                at = next(k for k, i in enumerate(items) if i["slot"] == 5) + 1
                items[at:at] = [{"stage_id": sid(conn, "Собеседование с руководителем"), "label": f"№{k}"}
                                for k in range(3)]
            stages.approve(conn, stages.propose(conn, vid, items, None, people["rec"], NOW)["id"], people["head"], NOW)
        length = len(stages.current_set(conn, vid)["items"])
        for _ in range(40):
            steps = rnd.randint(0, length)
            end = None if steps == length else rnd.choice([None, ("failed", "skills"), ("refused", "no_contact")])
            path(conn, vid, people["rec"], steps, end)
        rep = funnel_report.vacancy_report(conn, vid, today=TODAY)
        c = counts(rep)
        assert c[0][0] == 40
        for k in range(7):
            assert c[k + 1][0] == c[k][1], (n, ROWS[k])
        for r in rep["rows"]:
            assert r["passed"] <= r["entered"]
            if not r["in_set"]:
                assert r["entered"] == r["passed"]
        assert rep["conversion"]["hired"] == c[7][1]


def test_s16_cancelled_marks_not_counted_and_undo_restores_report(conn, prof, people):
    vid = reference_vacancy(conn, prof, people)
    before = funnel_report.vacancy_report(conn, vid, today=TODAY)
    e = db.val(conn, """SELECT candidate_id FROM ops.candidate_stage WHERE vacancy_id = %s AND outcome IS NULL
                        AND report_row = 'meeting'""", (vid,))
    stages.mark(conn, e, vid, open_slot(conn, e, vid), "passed", people["rec"], NOW)          # ошибочная
    assert funnel_report.vacancy_report(conn, vid, today=TODAY)["rows"] != before["rows"]
    stages.undo(conn, e, vid, people["rec"], NOW, comment="не тот кандидат")
    after = funnel_report.vacancy_report(conn, vid, today=TODAY)
    assert after["rows"] == before["rows"] and after["conversion"] == before["conversion"]
    # отказ, отменённый и отмеченный заново «прошёл», считается как «прошёл»
    j = db.val(conn, """SELECT candidate_id FROM ops.candidate_stage WHERE vacancy_id = %s AND outcome IS NULL
                        AND report_row = 'selected'""", (vid,))
    stages.mark(conn, j, vid, 1, "failed", people["rec"], NOW, reason="skills")
    stages.undo(conn, j, vid, people["rec"], NOW, comment="перепутал")
    stages.mark(conn, j, vid, 1, "passed", people["rec"], NOW)
    assert table(funnel_report.vacancy_report(conn, vid, today=TODAY))["selected"] == (10, 9, 90)


def test_s15_carried_counts_from_own_row(conn, prof, people):
    vid = new_vacancy(conn, prof, people)
    items = {i["report_row"]: i for i in stages.current_set(conn, vid)["items"]}
    cid = candidate(conn)
    db.run(conn, """INSERT INTO ops.candidate_stage (candidate_id, vacancy_id, slot, stage_id, report_row, carried)
                    VALUES (%s, %s, %s, %s, 'meeting', true)""",
           (cid, vid, items["meeting"]["slot"], items["meeting"]["stage_id"]))
    stages.mark(conn, cid, vid, items["meeting"]["slot"], "passed", people["rec"], NOW)
    path(conn, vid, people["rec"], 1)
    rep = funnel_report.vacancy_report(conn, vid, today=TODAY)
    assert table(rep)["selected"] == (1, 1, 100)
    assert table(rep)["contact"] == (1, 0, 0)
    assert table(rep)["screening"] == (0, 0, None)
    assert table(rep)["meeting"] == (1, 1, 100)
    assert table(rep)["security"] == (1, 0, 0)
    assert rep["carried"] == 1


# ------------------------------------------------------------------ S12, S13: рекрутер за год и отдел

def test_s12_recruiter_year_by_opened_at(conn, prof, people):
    a = new_vacancy(conn, prof, people, title="Инженер-электроник", opened=date(2026, 11, 3))
    b = new_vacancy(conn, prof, people, title="Монтажник РЭА", opened=date(2026, 3, 2))
    old = new_vacancy(conn, prof, people, title="Регулировщик", opened=date(2025, 12, 20))
    other = new_vacancy(conn, prof, people, title="Чужая вакансия", recruiter="rec2", opened=date(2026, 5, 5))
    for vid, steps in ((a, 8), (a, 2), (b, 1), (b, 0), (old, 8), (other, 8)):
        path(conn, vid, people["rec"], steps)
    rep = funnel_report.recruiter_report(conn, people["rec"], 2026, today=TODAY)
    assert rep["year"] == 2026 and rep["years"] == [2026, 2025]
    assert [v["title"] for v in rep["vacancies"]] == ["Инженер-электроник", "Монтажник РЭА"]
    assert counts(rep["total"])[:3] == [(4, 3), (3, 2), (2, 1)]
    assert rep["total"]["conversion"] == {"hired": 1, "selected": 4, "pct": 25}
    va = rep["vacancies"][0]
    assert va["id"] == a and counts(va)[:3] == [(2, 2), (2, 2), (2, 1)] and va["days"] == 20
    assert all("stages" not in r for r in rep["total"]["rows"])
    old_year = funnel_report.recruiter_report(conn, people["rec"], 2025, today=TODAY)
    assert [v["id"] for v in old_year["vacancies"]] == [old]
    empty = funnel_report.recruiter_report(conn, people["rec"], 2024, today=TODAY)
    assert empty["vacancies"] == [] and counts(empty["total"]) == [(0, 0)] * 8


def test_s13_department_sum_and_recruiters(conn, prof, people):
    a = new_vacancy(conn, prof, people, title="Инженер-электроник")
    b = new_vacancy(conn, prof, people, title="Чужая вакансия", recruiter="rec2", opened=date(2026, 5, 5))
    new_vacancy(conn, prof, people, title="Прошлый год", recruiter="rec2", opened=date(2025, 5, 5))
    for vid, steps in ((a, 8), (a, 0), (b, 1), (b, 1), (b, 0), (b, 0)):
        path(conn, vid, people["rec"], steps)
    dep = funnel_report.department_report(conn, 2026, today=TODAY)
    assert dep["year"] == 2026 and dep["years"] == [2026, 2025]
    assert counts(dep["total"])[:2] == [(6, 3), (3, 1)]
    assert dep["total"]["conversion"] == {"hired": 1, "selected": 6, "pct": 17}
    by = {r["id"]: r for r in dep["recruiters"]}
    assert set(by) == {people["rec"], people["rec2"]}
    assert counts(by[people["rec"]]["total"])[0] == (2, 1) and by[people["rec"]]["vacancies"] == 1
    assert counts(by[people["rec2"]]["total"])[0] == (4, 2)
    assert by[people["rec2"]]["name"] == db.val(conn, "SELECT full_name FROM app_user WHERE id = %s",
                                                (people["rec2"],))
    assert [r["name"] for r in dep["recruiters"]] == sorted(r["name"] for r in dep["recruiters"])
