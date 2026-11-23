"""Отчёт воронки (funnel-report, S11–S13, S16): 8 строк «вошли · прошли · %» по отметкам этапов, конверсия, срок
ведения без пауз; воронка рекрутера за год и отдела. Считает код по отметкам людей, без модели (AD-2).

Счёт идёт по неотменённым отметкам ops.candidate_stage пары кандидат×вакансия. У пары берутся первая и последняя
строки отчёта, где у неё есть отметки (lo, hi). Строка R: вошли — пары с lo ≤ R ≤ hi; прошли — из них пары с hi > R,
у «Пришедших в 1 день» — с исходом «прошёл». Отсюда: «вошёл на первом этапе строки, прошёл последний», «вошли R+1 =
прошли R», строка вне набора — вошли = прошли, перенесённый при выпуске (carried) считается со своей строки.
Раскрытие строки — этапы по slot: вошли — отметки этапа, прошли — из них «прошёл».
"""
from __future__ import annotations

from datetime import date, datetime

import psycopg

from .db import rows, val
from .stages import MSK

MIGRATION = "012_funnel_stages.sql"


class NotFound(LookupError):
    pass


def _pct(part: int, whole: int) -> int | None:
    """Процент целым, половина — вверх; вошли 0 — None («—» на экране)."""
    return (part * 200 + whole) // (whole * 2) if whole else None


def _today(today: date | None) -> date:
    return today or datetime.now(MSK).date()


def since(conn: psycopg.Connection) -> date | None:
    """«Учёт с» — день применения 012 по Москве (S15: учёт с чистого листа с даты выпуска)."""
    return val(conn, """SELECT (applied_at AT TIME ZONE 'Europe/Moscow')::date FROM ops.schema_migration
                        WHERE name = %s""", (MIGRATION,))


def _funnel_rows(conn) -> list[dict]:
    return rows(conn, "SELECT code, ord, title FROM ops.funnel_row ORDER BY ord")


def _counts(conn, ids: list[int]) -> dict[tuple[int, str], tuple[int, int]]:
    """(вакансия, строка) → (вошли, прошли) одним запросом."""
    return {(r["vacancy_id"], r["code"]): (r["entered"], r["passed"]) for r in rows(conn, """
        WITH p AS (
            SELECT cs.vacancy_id, cs.candidate_id, min(r.ord) AS lo, max(r.ord) AS hi,
                   bool_or(cs.report_row = 'first_day' AND cs.outcome = 'passed') AS came
            FROM ops.candidate_stage cs JOIN ops.funnel_row r ON r.code = cs.report_row
            WHERE cs.cancelled_at IS NULL AND cs.vacancy_id = ANY(%s)
            GROUP BY cs.vacancy_id, cs.candidate_id)
        SELECT p.vacancy_id, r.code,
               count(*) FILTER (WHERE p.lo <= r.ord AND r.ord <= p.hi) AS entered,
               count(*) FILTER (WHERE p.lo <= r.ord AND (r.ord < p.hi OR (r.code = 'first_day' AND p.came)))
                   AS passed
        FROM p CROSS JOIN ops.funnel_row r
        GROUP BY p.vacancy_id, r.code""", (ids,))}


def _stages(conn, vacancy_id: int) -> dict[str, list[dict]]:
    """Этапы строк для раскрытия: действующий набор по pos, затем этапы с отметками, которых в наборе уже нет."""
    cur = val(conn, """SELECT id FROM ops.stage_set WHERE vacancy_id = %s AND state = 'approved'
                       ORDER BY version DESC LIMIT 1""", (vacancy_id,))
    marks = {r["slot"]: r for r in rows(conn, """
        SELECT slot, min(report_row) AS report_row, min(stage_id) AS stage_id, count(*) AS entered,
               count(*) FILTER (WHERE outcome = 'passed') AS passed
        FROM ops.candidate_stage WHERE vacancy_id = %s AND cancelled_at IS NULL GROUP BY slot""", (vacancy_id,))}
    if cur:
        items = rows(conn, """SELECT i.slot, coalesce(i.label, s.title) AS title, s.report_row, true AS in_set
                              FROM ops.stage_set_item i JOIN ops.stage s ON s.id = i.stage_id
                              WHERE i.set_id = %s ORDER BY i.pos""", (cur,))
    else:                                       # вакансия v1.2.0 без набора — 8 стандартных (как stages.ensure_set)
        items = rows(conn, """SELECT r.ord AS slot, s.title, s.report_row, true AS in_set FROM ops.funnel_row r
                              JOIN ops.stage s ON s.report_row = r.code AND s.is_standard ORDER BY r.ord""")
    known = {i["slot"] for i in items}
    for slot in sorted(set(marks) - known):
        m = marks[slot]
        title = val(conn, """SELECT coalesce(i.label, s.title) FROM ops.stage_set_item i
                             JOIN ops.stage_set ss ON ss.id = i.set_id JOIN ops.stage s ON s.id = i.stage_id
                             WHERE ss.vacancy_id = %s AND i.slot = %s ORDER BY ss.version DESC LIMIT 1""",
                    (vacancy_id, slot)) or val(conn, "SELECT title FROM ops.stage WHERE id = %s", (m["stage_id"],))
        items.append({"slot": slot, "title": title, "report_row": m["report_row"], "in_set": False})
    out: dict[str, list[dict]] = {}
    for i in items:
        m = marks.get(i["slot"], {"entered": 0, "passed": 0})
        out.setdefault(i["report_row"], []).append({
            "slot": i["slot"], "title": i["title"], "in_set": i["in_set"], "entered": m["entered"],
            "passed": m["passed"], "pct": _pct(m["passed"], m["entered"])})
    return out


def _table(frows: list[dict], counts: dict[str, tuple[int, int]], in_set: dict[str, bool],
           stages: dict[str, list[dict]] | None) -> dict:
    out = []
    for r in frows:
        entered, passed = counts.get(r["code"], (0, 0))
        row = {"code": r["code"], "title": r["title"], "entered": entered, "passed": passed,
               "pct": _pct(passed, entered), "in_set": in_set.get(r["code"], True)}
        if stages is not None:
            row["stages"] = [s for s in stages.get(r["code"], []) if s["in_set"] or s["entered"]]
        out.append(row)
    hired, selected = out[-1]["passed"], out[0]["entered"]
    return {"rows": out, "conversion": {"hired": hired, "selected": selected, "pct": _pct(hired, selected)}}


def _vacancies(conn, today: date, *, vacancy_id: int | None = None, recruiter_id: int | None = None,
               year: int | None = None) -> list[dict]:
    """Вакансии с датами и сроком ведения: от открытия до закрытия (или сегодня) без дней пауз и закрытия."""
    return rows(conn, """
        SELECT v.id, v.title, v.state::text AS state, v.opened_at, v.closed_at, v.recruiter_id,
               (SELECT full_name FROM app_user WHERE id = v.recruiter_id) AS recruiter,
               coalesce((SELECT sum(GREATEST(coalesce(p.to_date, v.closed_at, %(today)s::date) - p.from_date, 0))
                         FROM ops.vacancy_pause p WHERE p.vacancy_id = v.id), 0)::int AS paused_days,
               (SELECT count(DISTINCT cs.candidate_id) FROM ops.candidate_stage cs
                WHERE cs.vacancy_id = v.id AND cs.carried AND cs.cancelled_at IS NULL)::int AS carried
        FROM vacancy v
        WHERE (%(vid)s::bigint IS NULL OR v.id = %(vid)s) AND (%(uid)s::int IS NULL OR v.recruiter_id = %(uid)s)
          AND (%(year)s::int IS NULL OR extract(year FROM v.opened_at) = %(year)s)
        ORDER BY v.opened_at DESC, v.id DESC""", {"today": today, "vid": vacancy_id, "uid": recruiter_id, "year": year})


def _reports(conn, vs: list[dict], today: date, *, stages: bool = True) -> list[dict]:
    """Отчёт каждой вакансии: даты, срок, строки с раскрытием и конверсия."""
    frows = _funnel_rows(conn)
    counts = _counts(conn, [v["id"] for v in vs])
    out = []
    for v in vs:
        st = _stages(conn, v["id"])
        in_set = {code: any(s["in_set"] for s in items) for code, items in st.items()}
        in_set.update({r["code"]: False for r in frows if r["code"] not in in_set})
        v["days"] = max(((v["closed_at"] or today) - v["opened_at"]).days - v["paused_days"], 0)
        v.update(_table(frows, {code: n for (vid, code), n in counts.items() if vid == v["id"]}, in_set,
                        st if stages else None))
        out.append(v)
    return out


def _total(conn, reports: list[dict]) -> dict:
    """Сумма вакансий по строкам: пары у каждой вакансии свои, поэтому складываются."""
    frows = _funnel_rows(conn)
    counts: dict[str, tuple[int, int]] = {}
    for rep in reports:
        for r in rep["rows"]:
            e, p = counts.get(r["code"], (0, 0))
            counts[r["code"]] = (e + r["entered"], p + r["passed"])
    in_set = {r["code"]: not reports or any(rep["rows"][n]["in_set"] for rep in reports)
              for n, r in enumerate(frows)}
    return _table(frows, counts, in_set, None)


def vacancy_report(conn: psycopg.Connection, vacancy_id: int, today: date | None = None) -> dict:
    """S11: воронка вакансии — 8 строк с раскрытием по этапам набора, конверсия, даты и срок ведения без пауз."""
    today = _today(today)
    vs = _vacancies(conn, today, vacancy_id=vacancy_id)
    if not vs:
        raise NotFound
    [rep] = _reports(conn, vs, today)
    rep["since"] = since(conn)
    return rep


def _years(conn, today: date, recruiter_id: int | None = None) -> list[int]:
    """Годы открытия вакансий (рекрутера или всех) и текущий — для переключателя года."""
    found = {int(r["y"]) for r in rows(conn, """SELECT DISTINCT extract(year FROM opened_at) AS y FROM vacancy
                                               WHERE %(uid)s::int IS NULL OR recruiter_id = %(uid)s""",
                                        {"uid": recruiter_id})}
    return sorted(found | {today.year}, reverse=True)


def recruiter_report(conn: psycopg.Connection, user_id: int, year: int, today: date | None = None) -> dict:
    """S12: воронка рекрутера — сумма его вакансий, открытых в этом году, и сами вакансии с воронками."""
    today = _today(today)
    vs = _reports(conn, _vacancies(conn, today, recruiter_id=user_id, year=year), today)
    return {"year": year, "years": _years(conn, today, user_id), "since": since(conn),
            "total": _total(conn, vs), "vacancies": vs}


def department_report(conn: psycopg.Connection, year: int, today: date | None = None) -> dict:
    """S13: воронка отдела — сумма всех вакансий года и сравнение рекрутеров (у каждого — сумма его вакансий)."""
    today = _today(today)
    vs = _reports(conn, _vacancies(conn, today, year=year), today, stages=False)
    by: dict[int, list[dict]] = {}
    for v in vs:
        if v["recruiter_id"] is not None:
            by.setdefault(v["recruiter_id"], []).append(v)
    recruiters = [{"id": uid, "name": items[0]["recruiter"], "vacancies": len(items), "total": _total(conn, items)}
                  for uid, items in by.items()]
    recruiters.sort(key=lambda r: (r["name"] or "", r["id"]))
    return {"year": year, "years": _years(conn, today), "since": since(conn), "total": _total(conn, vs),
            "recruiters": recruiters}
