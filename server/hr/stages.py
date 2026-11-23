"""Этапы вакансии (funnel-report, S1–S10, S16): библиотека этапов, набор вакансии, предложение и утверждение набора,
движение кандидата по этапам (вход, «прошёл / не прошёл / отказался», отмена ошибочной отметки).

Каждый этап библиотеки относится к одной из 8 строк отчёта (ops.funnel_row). У вакансии действует последний
утверждённый набор; рекрутер предлагает новый, руководитель ОП утверждает или отклоняет. slot — постоянный номер
этапа вакансии через версии набора: по нему отметки кандидатов остаются на своём этапе. Библиотека и наборы пишутся
в agent_change kind 'stages'; стандартный набор новой вакансии — не изменение поведения, в журнал не пишется.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import psycopg

from .db import j, one, rows, run, setting, val

FIXED_ROWS = ("selected", "first_day")        # S5: «Отобранные резюме» и «Пришедшие в 1 день» не убираются
OUTCOMES = ("passed", "failed", "refused")

# candidate.status — производный от отметок (для отката на v1.2.0, AD-8). Открытый этап — по строке отчёта;
# «Интервью» — «Телефонное интервью», чтобы не приписывать непройденное (обратная карта ops.funnel_carry()
# переносит его в «Собеседование»). На «Пришедших в 1 день» человек принял оффер, но ещё не вышел — «Оффер».
ROW_STATUS = {"selected": "picked_by_agent", "contact": "first_message_sent", "screening": "phone_interview",
              "interview": "phone_interview", "meeting": "manager_interview", "security": "security_check",
              "offer": "offer", "first_day": "offer"}
REFUSED_STATUS = {"no_contact": "refused_contact", "no_show": "no_show", "no_first_day": "no_show"}
MSK = ZoneInfo("Europe/Moscow")
CONTACT_STATUS = {"first_message_sent", "phone_interview", "manager_interview", "security_check", "offer"}


class NotFound(LookupError):
    pass


class Invalid(ValueError):
    pass


class Forbidden(PermissionError):
    """code — для ответа API (403 {error: code})."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


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


def standard_items(conn: psycopg.Connection) -> list[dict]:
    """8 стандартных этапов как набор v1 (pos = slot = порядок строки) — без записи в базу."""
    return rows(conn, """
        SELECT r.ord AS pos, r.ord AS slot, s.id AS stage_id, NULL::text AS label, s.title, s.report_row,
               s.is_standard, s.is_active
        FROM ops.funnel_row r JOIN ops.stage s ON s.report_row = r.code AND s.is_standard ORDER BY r.ord""")


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


# ------------------------------------------------------------------ движение кандидата (S6–S10)

def _status(conn, candidate_id: int, to_status: str, user_id: int, now: datetime, comment: str | None = None,
            reject_reason: str | None = None) -> None:
    """Производный candidate.status и строка status_history — только когда статус меняется."""
    c = one(conn, "SELECT status::text AS status FROM candidate WHERE id = %s FOR UPDATE", (candidate_id,))
    if c["status"] == to_status:
        return
    contact = to_status in CONTACT_STATUS
    run(conn, """UPDATE candidate SET status = %s,
                     last_contact_at = CASE WHEN %s THEN %s ELSE last_contact_at END,
                     first_contact_at = CASE WHEN %s AND first_contact_at IS NULL THEN %s ELSE first_contact_at END,
                     reject_reason = coalesce(%s, reject_reason)
                 WHERE id = %s""", (to_status, contact, now, contact, now, reject_reason, candidate_id))
    run(conn, """INSERT INTO status_history (candidate_id, from_status, to_status, changed_by, changed_at, comment)
                 VALUES (%s, %s, %s, %s, %s, %s)""", (candidate_id, c["status"], to_status, user_id, now, comment))


def _stage_title(conn, stage_id: int, set_id: int | None, slot: int) -> str:
    label = val(conn, "SELECT label FROM ops.stage_set_item WHERE set_id = %s AND slot = %s", (set_id, slot)) \
        if set_id else None
    return label or val(conn, "SELECT title FROM ops.stage WHERE id = %s", (stage_id,))


def _open(conn, candidate_id: int, vacancy_id: int) -> dict | None:
    return one(conn, """SELECT id, slot, stage_id, report_row, interview_id FROM ops.candidate_stage
                        WHERE candidate_id = %s AND vacancy_id = %s AND outcome IS NULL AND cancelled_at IS NULL
                        FOR UPDATE""", (candidate_id, vacancy_id))


def _put(conn, candidate_id: int, vacancy_id: int, item: dict, user_id: int, now: datetime) -> int:
    return val(conn, """INSERT INTO ops.candidate_stage (candidate_id, vacancy_id, slot, stage_id, report_row,
                                                         entered_by, entered_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
               (candidate_id, vacancy_id, item["slot"], item["stage_id"], item["report_row"], user_id, now))


def enter(conn: psycopg.Connection, candidate_id: int, vacancy_id: int, user_id: int, now: datetime) -> int | None:
    """S10: взятый в работу — на первый этап набора вакансии («Отобранные резюме»). Набора нет (вакансия v1.2.0) —
    стандартный v1 в той же транзакции. Новых людей берёт только открытая вакансия (на паузе и закрытая — нет).
    Пара уже в воронке — ничего не меняется; возвращается slot открытого этапа (или None, если исход есть)."""
    v = one(conn, "SELECT id, state FROM vacancy WHERE id = %s FOR UPDATE", (vacancy_id,))
    if not v or not val(conn, "SELECT 1 FROM candidate WHERE id = %s", (candidate_id,)):
        raise NotFound
    if val(conn, """SELECT 1 FROM ops.candidate_stage WHERE candidate_id = %s AND vacancy_id = %s
                    AND cancelled_at IS NULL LIMIT 1""", (candidate_id, vacancy_id)):
        cur = _open(conn, candidate_id, vacancy_id)
        return cur["slot"] if cur else None
    if v["state"] != "open":
        raise Conflict("vacancy_not_open", "вакансия не открыта: новых людей в воронку не берут")
    ensure_set(conn, vacancy_id)
    item = current_set(conn, vacancy_id)["items"][0]
    run(conn, """INSERT INTO ops.candidate_vacancy (candidate_id, vacancy_id, added_by, added_at)
                 VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING""", (candidate_id, vacancy_id, user_id, now))
    _put(conn, candidate_id, vacancy_id, item, user_id, now)
    _status(conn, candidate_id, ROW_STATUS[item["report_row"]], user_id, now,
            f"в воронку: этап «{item['label'] or item['title']}»")
    return item["slot"]


def _reason(conn, outcome: str, reason: str | None, comment: str | None, report_row: str) -> dict:
    """S7: причина своего исхода и строки; «Другое» — с комментарием не короче stage_fail_other_min."""
    if not reason:
        raise Invalid("причина обязательна")
    r = one(conn, "SELECT code, title, outcome, only_row, needs_comment FROM ops.fail_reason WHERE code = %s",
            (reason,))
    if not r or (r["outcome"] and r["outcome"] != outcome):
        raise Invalid("причина не подходит к исходу")
    if r["only_row"] and r["only_row"] != report_row:
        title = val(conn, "SELECT title FROM ops.funnel_row WHERE code = %s", (r["only_row"],))
        raise Invalid(f"причина «{r['title']}» — только на этапе «{title}»")
    if r["needs_comment"]:
        minimum = int(setting(conn, "stage_fail_other_min", 100))
        if len((comment or "").strip()) < minimum:
            raise Invalid(f"комментарий к причине «{r['title']}» не короче {minimum} знаков")
    return r


def _hired(conn, vacancy_id: int) -> int:
    """Вышедшие: пары, у которых пройдены этапы «Пришедшие в 1 день» и нет ни открытого этапа, ни отказа."""
    return val(conn, """
        SELECT count(DISTINCT cs.candidate_id) FROM ops.candidate_stage cs
        WHERE cs.vacancy_id = %s AND cs.cancelled_at IS NULL AND cs.report_row = 'first_day' AND cs.outcome = 'passed'
          AND NOT EXISTS (SELECT 1 FROM ops.candidate_stage o
                          WHERE o.candidate_id = cs.candidate_id AND o.vacancy_id = cs.vacancy_id
                            AND o.cancelled_at IS NULL AND o.outcome IS DISTINCT FROM 'passed')""", (vacancy_id,))


def mark(conn: psycopg.Connection, candidate_id: int, vacancy_id: int, slot: int, outcome: str, user_id: int,
         now: datetime, *, reason: str | None = None, comment: str | None = None,
         scheduled_at: datetime | None = None) -> dict:
    """S6–S9: исход открытого этапа пары. «Прошёл» — на следующий этап действующего набора (назад и через этап —
    409 stale), на последнем — «вышел» (hired) и offer_close, когда вышедших не меньше ставок: закрывает вакансию
    рекрутер прежним close. «Не прошёл» / «Отказался» — с причиной. На закрытой вакансии «прошёл» нельзя, отказ
    с причиной — можно; на паузе отмечать можно."""
    if outcome not in OUTCOMES:
        raise Invalid("исход этапа")
    if scheduled_at and outcome != "passed":
        raise Invalid("дата встречи — только когда кандидат прошёл на следующий этап")
    v = one(conn, "SELECT id, state, headcount FROM vacancy WHERE id = %s FOR UPDATE", (vacancy_id,))
    if not v or not val(conn, """SELECT 1 FROM ops.candidate_stage WHERE candidate_id = %s AND vacancy_id = %s
                                 AND cancelled_at IS NULL LIMIT 1""", (candidate_id, vacancy_id)):
        raise NotFound
    cur = _open(conn, candidate_id, vacancy_id)
    if not cur or cur["slot"] != slot:
        raise Conflict("stale", "этап кандидата уже другой — обновите страницу")
    closed = v["state"] in ("closed", "cancelled")
    if outcome == "passed" and closed:
        raise Conflict("vacancy_closed", "вакансия закрыта: «прошёл» отметить нельзя, отказ с причиной — можно")
    comment = (comment or "").strip() or None
    r = None if outcome == "passed" else _reason(conn, outcome, reason, comment, cur["report_row"])
    run(conn, """UPDATE ops.candidate_stage SET outcome = %s, reason = %s, comment = %s, decided_by = %s,
                     decided_at = %s WHERE id = %s""",
        (outcome, r["code"] if r else None, comment, user_id, now, cur["id"]))
    st = current_set(conn, vacancy_id)
    title = _stage_title(conn, cur["stage_id"], st["id"] if st else None, slot)
    if r:
        why = r["title"] + (f": {comment}" if comment else "")
        to = "rejected_by_us" if outcome == "failed" else REFUSED_STATUS.get(r["code"], "rejected_by_candidate")
        _status(conn, candidate_id, to, user_id, now, f"этап «{title}»: {why}", reject_reason=why)
        return {"next_slot": None, "offer_close": False}
    items = st["items"] if st else []
    pos = next((n for n, i in enumerate(items) if i["slot"] == slot), None)
    if pos is None:
        raise Conflict("stale", "этапа нет в действующем наборе — обновите страницу")
    done = {m["slot"] for m in rows(conn, """SELECT slot FROM ops.candidate_stage WHERE candidate_id = %s
                                            AND vacancy_id = %s AND cancelled_at IS NULL""",
                                    (candidate_id, vacancy_id))}
    nxt = next((i for i in items[pos + 1:] if i["slot"] not in done), None)
    if nxt:
        opened = _put(conn, candidate_id, vacancy_id, nxt, user_id, now)
        _status(conn, candidate_id, ROW_STATUS[nxt["report_row"]], user_id, now,
                f"этап «{title}» пройден, дальше — «{nxt['label'] or nxt['title']}»")
        if scheduled_at:   # встреча видна в «Сегодня и завтра»; вид — по строке отчёта следующего этапа
            meeting = val(conn, """INSERT INTO interview (candidate_id, vacancy_id, kind, scheduled_at)
                                   VALUES (%s, %s, %s, %s) RETURNING id""",
                          (candidate_id, vacancy_id, "manager" if nxt["report_row"] == "meeting" else "phone",
                           scheduled_at))
            run(conn, "UPDATE ops.candidate_stage SET interview_id = %s WHERE id = %s", (meeting, opened))
        return {"next_slot": nxt["slot"], "offer_close": False}
    if scheduled_at:
        raise Invalid("дата встречи — на последнем этапе встречи нет")
    _status(conn, candidate_id, "hired", user_id, now, f"этап «{title}» пройден: вышел")
    return {"next_slot": None, "offer_close": not closed and _hired(conn, vacancy_id) >= v["headcount"]}


def _can_approve(conn, user_id: int) -> bool:
    return bool(val(conn, """SELECT 1 FROM app_user u JOIN role_permission rp ON rp.role = u.role
                             WHERE u.id = %s AND rp.permission = 'stage.approve'""", (user_id,)))


def undo(conn: psycopg.Connection, candidate_id: int, vacancy_id: int, user_id: int, now: datetime, *,
         comment: str) -> dict:
    """S16: отмена последней по decided_at отметки пары. Свою — в тот же день по Москве; чужую и позже — только
    с правом stage.approve (руководитель ОП). Отметка и открытая после неё помечаются отменёнными (не удаляются),
    встреча, назначенная отменённой отметкой, — result 'cancelled'; этап снова открыт копией с прежним входом
    (этап убран из набора — первый непройденный той же строки, строки нет — 409 stage_removed), статус пересчитан,
    отмена — в audit_log."""
    comment = (comment or "").strip()
    v = one(conn, "SELECT id, state FROM vacancy WHERE id = %s FOR UPDATE", (vacancy_id,))
    if not v or not val(conn, """SELECT 1 FROM ops.candidate_stage WHERE candidate_id = %s AND vacancy_id = %s
                                 AND cancelled_at IS NULL LIMIT 1""", (candidate_id, vacancy_id)):
        raise NotFound
    if not comment:
        raise Invalid("причина отмены")
    if v["state"] in ("closed", "cancelled"):
        raise Conflict("vacancy_closed", "вакансия закрыта: отметки не отменяются")
    last = one(conn, """SELECT id, slot, stage_id, report_row, entered_by, entered_at, carried, outcome, decided_by,
                               decided_at
                        FROM ops.candidate_stage
                        WHERE candidate_id = %s AND vacancy_id = %s AND cancelled_at IS NULL AND outcome IS NOT NULL
                        ORDER BY decided_at DESC, id DESC LIMIT 1 FOR UPDATE""", (candidate_id, vacancy_id))
    if not last:
        raise Conflict("nothing_to_undo", "у кандидата нет отметок, которые можно отменить")
    own_today = last["decided_by"] == user_id and \
        last["decided_at"].astimezone(MSK).date() == now.astimezone(MSK).date()
    if not own_today and not _can_approve(conn, user_id):
        raise Forbidden("undo_expired", "Отменить может руководитель ОП")
    opened = _open(conn, candidate_id, vacancy_id)
    ids = [last["id"]] + ([opened["id"]] if opened else [])
    st = current_set(conn, vacancy_id)
    title = _stage_title(conn, last["stage_id"], st["id"] if st else None, last["slot"])
    back = _undo_target(conn, candidate_id, vacancy_id, last, ids, st, title)
    run(conn, """UPDATE ops.candidate_stage SET cancelled_by = %s, cancelled_at = %s, cancel_comment = %s
                 WHERE id = ANY(%s)""", (user_id, now, comment, ids))
    run(conn, """INSERT INTO ops.candidate_stage (candidate_id, vacancy_id, slot, stage_id, report_row, entered_by,
                                                  entered_at, carried)
                 VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
        (candidate_id, vacancy_id, back["slot"], back["stage_id"], back["report_row"], last["entered_by"],
         last["entered_at"], last["carried"]))
    meeting = opened["interview_id"] if opened else None
    if meeting:   # встреча, назначенная отменённой отметкой, уходит из «Сегодня и завтра»; строка остаётся
        run(conn, "UPDATE interview SET result = 'cancelled' WHERE id = %s AND result IS NULL", (meeting,))
    _status(conn, candidate_id, ROW_STATUS[last["report_row"]], user_id, now,
            f"отменена отметка этапа «{title}»: {comment}")
    run(conn, """INSERT INTO audit_log (user_id, action, object_type, object_id, detail, at)
                 VALUES (%s, 'edit', 'candidate_stage', %s, %s, %s)""",
        (user_id, last["id"], j({"undo": True, "candidate_id": candidate_id, "vacancy_id": vacancy_id,
                                 "slot": last["slot"], "outcome": last["outcome"], "cancelled": ids,
                                 "reopened": back["slot"], "interview": meeting, "comment": comment}), now))
    return {"slot": back["slot"]}


def _undo_target(conn, candidate_id: int, vacancy_id: int, last: dict, cancelled: list[int], st: dict | None,
                 title: str) -> dict:
    """Куда вернуть человека при отмене: на этап отметки, а если его убрали из набора после неё — на первый ещё не
    пройденный этап той же строки отчёта. Иначе человек встал бы на этап вне доски, и «прошёл» отвечал бы stale."""
    if not st or any(i["slot"] == last["slot"] for i in st["items"]):
        return last
    done = {m["slot"] for m in rows(conn, """SELECT slot FROM ops.candidate_stage WHERE candidate_id = %s
                                            AND vacancy_id = %s AND cancelled_at IS NULL AND NOT id = ANY(%s)""",
                                    (candidate_id, vacancy_id, cancelled))}
    same = [i for i in st["items"] if i["report_row"] == last["report_row"] and i["slot"] not in done]
    if not same:
        row = val(conn, "SELECT title FROM ops.funnel_row WHERE code = %s", (last["report_row"],))
        raise Conflict("stage_removed", f"этап «{title}» убран из набора вакансии, другого этапа строки «{row}» в "
                       "наборе нет — отменить отметку нельзя")
    return same[0]


def _journal(conn, before: dict | None, after: dict | None, user_id: int, now: datetime, note: str) -> None:
    run(conn, """INSERT INTO agent_change (kind, before, after, applied_by, applied_at, note)
                 VALUES ('stages', %s, %s, %s, %s, %s)""",
        (j(before) if before is not None else None, j(after) if after is not None else None, user_id, now, note))
