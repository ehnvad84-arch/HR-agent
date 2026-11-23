"""Этапы вакансии (funnel-report, S1–S5): библиотека этапов, набор вакансии, предложение и утверждение набора.

Каждый этап библиотеки относится к одной из 8 строк отчёта (ops.funnel_row). У вакансии действует последний
утверждённый набор; рекрутер предлагает новый, руководитель ОП утверждает или отклоняет. slot — постоянный номер
этапа вакансии через версии набора: по нему отметки кандидатов остаются на своём этапе. Библиотека и наборы пишутся
в agent_change kind 'stages'; стандартный набор новой вакансии — не изменение поведения, в журнал не пишется.
"""
from __future__ import annotations

from datetime import datetime

import psycopg

from .db import j, one, rows, run, val

FIXED_ROWS = ("selected", "first_day")        # S5: «Отобранные резюме» и «Пришедшие в 1 день» не убираются


class NotFound(LookupError):
    pass


class Invalid(ValueError):
    pass


class Conflict(RuntimeError):
    """code — для ответа API (409 {error: code}), data — подробности (stage_busy: slot и число людей)."""

    def __init__(self, code: str, message: str, data: dict | None = None):
        super().__init__(message)
        self.code = code
        self.data = data or {}


def _people(n: int) -> str:
    tail = n % 100
    word = "человека" if tail % 10 in (2, 3, 4) and tail // 10 != 1 else "человек"
    return f"{n} {word}"


# ------------------------------------------------------------------ библиотека (S1)

def library(conn: psycopg.Connection) -> dict:
    """Строки отчёта, этапы (в скольких действующих наборах вакансий) и причины исхода."""
    return {
        "rows": rows(conn, "SELECT code, ord, title FROM ops.funnel_row ORDER BY ord"),
        "stages": rows(conn, """
            WITH cur AS (SELECT DISTINCT ON (vacancy_id) vacancy_id, id FROM ops.stage_set
                         WHERE state = 'approved' ORDER BY vacancy_id, version DESC)
            SELECT s.id, s.title, s.report_row, s.is_standard, s.is_active,
                   (SELECT count(DISTINCT cur.vacancy_id) FROM cur JOIN ops.stage_set_item i ON i.set_id = cur.id
                    WHERE i.stage_id = s.id) AS vacancies
            FROM ops.stage s JOIN ops.funnel_row r ON r.code = s.report_row
            ORDER BY r.ord, NOT s.is_standard, s.title"""),
        "reasons": rows(conn, """SELECT code, title, outcome, only_row, needs_comment FROM ops.fail_reason
                                 ORDER BY ord"""),
    }


def add_stage(conn: psycopg.Connection, title: str, report_row: str, user_id: int, now: datetime) -> int:
    title = (title or "").strip()
    if not title:
        raise Invalid("название этапа")
    row = one(conn, "SELECT code, title FROM ops.funnel_row WHERE code = %s", (report_row,))
    if not row:
        raise Invalid("строка отчёта")
    if val(conn, "SELECT 1 FROM ops.stage WHERE lower(title) = lower(%s)", (title,)):
        raise Conflict("duplicate", f"этап «{title}» уже есть в справочнике")
    sid = val(conn, """INSERT INTO ops.stage (title, report_row, created_by, created_at) VALUES (%s, %s, %s, %s)
                       RETURNING id""", (title, report_row, user_id, now))
    _journal(conn, None, {"stage_id": sid, "title": title, "report_row": report_row}, user_id, now,
             f"в справочник добавлен этап «{title}» — строка отчёта «{row['title']}»")
    return sid


def retire_stage(conn: psycopg.Connection, stage_id: int, user_id: int, now: datetime) -> None:
    """Этап выводится из работы: в новые наборы его не берут, действующие наборы и отметки не меняются.
    Стандартные этапы — основа набора новой вакансии, их не выводят."""
    st = one(conn, "SELECT id, title, is_standard, is_active FROM ops.stage WHERE id = %s FOR UPDATE", (stage_id,))
    if not st:
        raise NotFound
    if st["is_standard"]:
        raise Conflict("standard", "стандартный этап нельзя вывести из работы")
    if not st["is_active"]:
        raise Conflict("retired", "этап уже выведен из работы")
    run(conn, "UPDATE ops.stage SET is_active = false WHERE id = %s", (stage_id,))
    _journal(conn, {"stage_id": stage_id, "is_active": True}, {"stage_id": stage_id, "is_active": False}, user_id,
             now, f"этап «{st['title']}» выведен из работы")


# ------------------------------------------------------------------ набор вакансии (S2)

def _items(conn, set_id: int) -> list[dict]:
    return rows(conn, """
        SELECT i.pos, i.slot, i.stage_id, i.label, s.title, s.report_row, s.is_standard, s.is_active
        FROM ops.stage_set_item i JOIN ops.stage s ON s.id = i.stage_id
        WHERE i.set_id = %s ORDER BY i.pos""", (set_id,))


def _set(conn, set_id: int) -> dict:
    s = one(conn, """SELECT ss.id, ss.vacancy_id, ss.version, ss.state, ss.note, ss.proposed_by, ss.proposed_at,
                            ss.decided_by, ss.decided_at, ss.decision_comment,
                            (SELECT full_name FROM app_user WHERE id = ss.proposed_by) AS proposed_by_name,
                            (SELECT full_name FROM app_user WHERE id = ss.decided_by) AS decided_by_name
                     FROM ops.stage_set ss WHERE ss.id = %s""", (set_id,))
    s["items"] = _items(conn, set_id)
    return s


def current_set(conn: psycopg.Connection, vacancy_id: int) -> dict | None:
    """Действующий набор — последний утверждённый."""
    sid = val(conn, """SELECT id FROM ops.stage_set WHERE vacancy_id = %s AND state = 'approved'
                       ORDER BY version DESC LIMIT 1""", (vacancy_id,))
    return _set(conn, sid) if sid else None


def ensure_set(conn: psycopg.Connection, vacancy_id: int) -> int:
    """S2: у вакансии без набора — утверждённый v1 из 8 стандартных этапов (pos = slot = порядок строки), как
    ops.funnel_carry(). Вызывают создание вакансии и вход в воронку вакансии, заведённой v1.2.0."""
    sid = val(conn, """SELECT id FROM ops.stage_set WHERE vacancy_id = %s AND state = 'approved'
                       ORDER BY version DESC LIMIT 1""", (vacancy_id,))
    if sid:
        return sid
    sid = val(conn, """INSERT INTO ops.stage_set (vacancy_id, version, state, note, decided_at)
                       VALUES (%s, 1, 'approved', 'стандартный набор', now()) RETURNING id""", (vacancy_id,))
    run(conn, """INSERT INTO ops.stage_set_item (set_id, pos, slot, stage_id)
                 SELECT %s, r.ord, r.ord, s.id FROM ops.funnel_row r
                 JOIN ops.stage s ON s.report_row = r.code AND s.is_standard""", (sid,))
    return sid


def _people_on(conn, vacancy_id: int) -> dict[int, int]:
    """Люди на этапах вакансии: открытые неотменённые отметки по slot."""
    return {r["slot"]: r["n"] for r in rows(conn, """
        SELECT slot, count(*) AS n FROM ops.candidate_stage
        WHERE vacancy_id = %s AND outcome IS NULL AND cancelled_at IS NULL GROUP BY slot ORDER BY slot""",
                                             (vacancy_id,))}


def sets(conn: psycopg.Connection, vacancy_id: int) -> dict:
    """Действующий набор (с людьми по slot), ждущий утверждения и отклонённый, если отклонение — последнее
    решение по набору вакансии (S3, S4)."""
    if not val(conn, "SELECT 1 FROM vacancy WHERE id = %s", (vacancy_id,)):
        raise NotFound
    cur = current_set(conn, vacancy_id)
    if cur:
        cur["people"] = _people_on(conn, vacancy_id)
    last = one(conn, """SELECT id, state FROM ops.stage_set WHERE vacancy_id = %s ORDER BY version DESC LIMIT 1""",
               (vacancy_id,))
    proposed = val(conn, "SELECT id FROM ops.stage_set WHERE vacancy_id = %s AND state = 'proposed'", (vacancy_id,))
    return {"current": cur, "proposed": _set(conn, proposed) if proposed else None,
            "declined": _set(conn, last["id"]) if last and last["state"] == "declined" else None}


# ------------------------------------------------------------------ предложение и утверждение (S3–S5)

def _summary(s: dict | None) -> dict | None:
    if not s:
        return None
    return {"set_id": s["id"], "version": s["version"], "state": s["state"],
            "stages": [i["label"] or i["title"] for i in s["items"]]}


def _vacancy_title(conn, vacancy_id: int) -> str:
    return val(conn, "SELECT title FROM vacancy WHERE id = %s", (vacancy_id,))


def _check_busy(conn, vacancy_id: int, cur: dict, new_slots: set[int]) -> None:
    people = _people_on(conn, vacancy_id)
    for item in cur["items"]:
        if item["slot"] not in new_slots and people.get(item["slot"]):
            n = people[item["slot"]]
            raise Conflict("stage_busy", f"На этапе «{item['label'] or item['title']}» {_people(n)} — сначала "
                           "переведите их", {"slot": item["slot"], "count": n})


def _validate(conn, vacancy_id: int, cur: dict, items: list[dict]) -> list[dict]:
    """Строки набора по порядку отчёта, «Отобранные резюме» и «Пришедшие в 1 день» есть, slot — только свои и
    со своим этапом, новые этапы — действующие. Возвращает этапы с назначенными slot."""
    if not items:
        raise Invalid("набор пуст")
    by_slot = {i["slot"]: i for i in cur["items"]}
    stage_ids = list({int(i.get("stage_id") or 0) for i in items})
    lib = {s["id"]: s for s in rows(conn, "SELECT id, title, report_row, is_active FROM ops.stage WHERE id = ANY(%s)",
                                    (stage_ids,))}
    order = {r["code"]: r["ord"] for r in rows(conn, "SELECT code, ord FROM ops.funnel_row")}
    next_slot = 1 + (val(conn, """SELECT max(i.slot) FROM ops.stage_set_item i JOIN ops.stage_set s ON s.id = i.set_id
                                  WHERE s.vacancy_id = %s""", (vacancy_id,)) or 0)
    out, seen, last = [], set(), 0
    for item in items:
        st = lib.get(int(item.get("stage_id") or 0))
        if not st:
            raise Invalid("этап не найден в справочнике")
        slot = item.get("slot")
        if slot is not None:
            old = by_slot.get(slot)
            if not old or old["stage_id"] != st["id"]:
                raise Invalid(f"этап «{st['title']}» не из действующего набора")
            if slot in seen:
                raise Invalid(f"этап «{st['title']}» в наборе дважды")
        else:
            if not st["is_active"]:
                raise Invalid(f"этап «{st['title']}» выведен из работы")
            slot, next_slot = next_slot, next_slot + 1
        seen.add(slot)
        if order[st["report_row"]] < last:
            raise Invalid("порядок этапов: строки отчёта идут по порядку воронки")
        last = order[st["report_row"]]
        label = (item.get("label") or "").strip() or None
        out.append({"slot": slot, "stage_id": st["id"], "label": label})
    present = {lib[i["stage_id"]]["report_row"] for i in out}
    if not set(FIXED_ROWS) <= present:
        raise Invalid("«Отобранные резюме» и «Пришедшие в 1 день» в наборе обязательны")
    if [(i["slot"], i["stage_id"], i["label"]) for i in out] == \
            [(i["slot"], i["stage_id"], i["label"]) for i in cur["items"]]:
        raise Invalid("набор не изменился")
    return out


def propose(conn: psycopg.Connection, vacancy_id: int, items: list[dict], note: str | None, user_id: int,
            now: datetime) -> dict:
    """S3: новый набор ждёт утверждения, вакансия идёт по действующему. На утверждении — не больше одного."""
    if not one(conn, "SELECT id FROM vacancy WHERE id = %s FOR UPDATE", (vacancy_id,)):
        raise NotFound
    if val(conn, "SELECT 1 FROM ops.stage_set WHERE vacancy_id = %s AND state = 'proposed'", (vacancy_id,)):
        raise Conflict("pending_exists", "новый набор уже ждёт утверждения — отзовите его или дождитесь решения")
    ensure_set(conn, vacancy_id)
    cur = current_set(conn, vacancy_id)
    new = _validate(conn, vacancy_id, cur, items)
    _check_busy(conn, vacancy_id, cur, {i["slot"] for i in new})
    version = 1 + val(conn, "SELECT max(version) FROM ops.stage_set WHERE vacancy_id = %s", (vacancy_id,))
    note = (note or "").strip() or None
    sid = val(conn, """INSERT INTO ops.stage_set (vacancy_id, version, state, note, proposed_by, proposed_at)
                       VALUES (%s, %s, 'proposed', %s, %s, %s) RETURNING id""",
              (vacancy_id, version, note, user_id, now))
    for pos, item in enumerate(new, 1):
        run(conn, "INSERT INTO ops.stage_set_item (set_id, pos, slot, stage_id, label) VALUES (%s, %s, %s, %s, %s)",
            (sid, pos, item["slot"], item["stage_id"], item["label"]))
    _journal(conn, _summary(cur), _summary(_set(conn, sid)), user_id, now,
             f"набор этапов вакансии «{_vacancy_title(conn, vacancy_id)}» (версия {version}) ждёт утверждения")
    return {"id": sid, "version": version}


def _proposed(conn, set_id: int) -> dict:
    s = one(conn, "SELECT id, vacancy_id, version, state FROM ops.stage_set WHERE id = %s FOR UPDATE", (set_id,))
    if not s:
        raise NotFound
    if s["state"] != "proposed":
        raise Conflict("not_proposed", "набор не ждёт утверждения")
    return s


def withdraw(conn: psycopg.Connection, set_id: int, user_id: int, now: datetime) -> str:
    s = _proposed(conn, set_id)
    run(conn, "UPDATE ops.stage_set SET state = 'withdrawn', decided_by = %s, decided_at = %s WHERE id = %s",
        (user_id, now, set_id))
    _journal(conn, {"set_id": set_id, "version": s["version"], "state": "proposed"},
             {"set_id": set_id, "version": s["version"], "state": "withdrawn"}, user_id, now,
             f"набор этапов вакансии «{_vacancy_title(conn, s['vacancy_id'])}» (версия {s['version']}) отозван")
    return "withdrawn"


def approve(conn: psycopg.Connection, set_id: int, user_id: int, now: datetime) -> str:
    """S4: утверждённый набор действует сразу. Этап с людьми не убрать (S5) — проверка повторяется: пока набор
    ждал, человек мог дойти до убираемого этапа."""
    s = _proposed(conn, set_id)
    run(conn, "SELECT id FROM vacancy WHERE id = %s FOR UPDATE", (s["vacancy_id"],))
    cur = current_set(conn, s["vacancy_id"])
    new = _set(conn, set_id)
    if cur:
        _check_busy(conn, s["vacancy_id"], cur, {i["slot"] for i in new["items"]})
    run(conn, "UPDATE ops.stage_set SET state = 'approved', decided_by = %s, decided_at = %s WHERE id = %s",
        (user_id, now, set_id))
    new["state"] = "approved"
    _journal(conn, _summary(cur), _summary(new), user_id, now,
             f"утверждён набор этапов вакансии «{_vacancy_title(conn, s['vacancy_id'])}» (версия {s['version']})")
    return "approved"


def decline(conn: psycopg.Connection, set_id: int, comment: str, user_id: int, now: datetime) -> str:
    """S4: отклонение — с комментарием, его видит рекрутер; вакансия идёт по действующему набору."""
    s = _proposed(conn, set_id)
    comment = (comment or "").strip()
    if not comment:
        raise Invalid("комментарий к отклонению")
    run(conn, """UPDATE ops.stage_set SET state = 'declined', decided_by = %s, decided_at = %s, decision_comment = %s
                 WHERE id = %s""", (user_id, now, comment, set_id))
    _journal(conn, {"set_id": set_id, "version": s["version"], "state": "proposed"},
             {"set_id": set_id, "version": s["version"], "state": "declined", "comment": comment}, user_id, now,
             f"отклонён набор этапов вакансии «{_vacancy_title(conn, s['vacancy_id'])}» (версия {s['version']})")
    return "declined"


def _journal(conn, before: dict | None, after: dict | None, user_id: int, now: datetime, note: str) -> None:
    run(conn, """INSERT INTO agent_change (kind, before, after, applied_by, applied_at, note)
                 VALUES ('stages', %s, %s, %s, %s, %s)""",
        (j(before) if before is not None else None, j(after) if after is not None else None, user_id, now, note))
