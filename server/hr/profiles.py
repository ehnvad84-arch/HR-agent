"""Профили должностей (S25–S27): правка создаёт новую версию «ждёт утверждения», выдача считается
по прежней до утверждения (пункт 21); утверждает роль с profile.approve (пункт 32), затем база
пересчитывается в фоне (pipeline.rescore_profile). Каждое изменение пишется в agent_change.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path, PurePath

import psycopg

from . import pipeline, scoring
from .config import get_config
from .db import j, one, rows, run, setting, val
from .scoring import FEATURE_LABELS

EDITABLE = ("title", "geo", "salary_min", "salary_max", "must_have", "nice_to_have", "stop_factors", "weights",
            "keywords", "donor_companies")
JSON_FIELDS = {"must_have", "nice_to_have", "stop_factors", "weights"}
GEO = {"krasnodar", "remote_ru", "relocation_ru"}


class Conflict(RuntimeError):
    pass


class NotFound(LookupError):
    pass


class Invalid(ValueError):
    pass


def is_empty(p: dict) -> bool:
    """Пустая заготовка из 1С (S47): ни условий, ни весов, ни ключевых слов — утверждать нечего."""
    if "empty" in p:
        return bool(p["empty"])
    return not (p.get("must_have") or p.get("nice_to_have") or p.get("weights") or p.get("keywords"))


def state(p: dict) -> str:
    if p["approved_at"]:
        return "approved"
    if is_empty(p):
        return "empty"   # критерии не заполнены
    return "draft" if p.get("di_uploaded_at") else "pending"   # draft — собрана из новой ДИ, человек не правил


_LIST = """
    SELECT rp.id, rp.code, rp.title, rp.version, rp.approved_at, rp.di_uploaded_at, rp.valid_from, rp.geo,
           d.code AS dept_code, d.title AS dept,
           (rp.must_have = '[]' AND rp.nice_to_have = '[]' AND rp.weights = '{}' AND rp.keywords = '{}') AS empty,
           (SELECT max(x.id) FROM role_profile x WHERE x.code = rp.code AND x.approved_at IS NULL
              AND x.id > rp.id) AS pending_id
    FROM role_profile rp JOIN department d ON d.id = rp.department_id
    WHERE rp.is_active AND rp.id = coalesce(
        (SELECT x.id FROM role_profile x WHERE x.code = rp.code AND x.is_active AND x.approved_at IS NOT NULL
         ORDER BY x.approved_at DESC, x.id DESC LIMIT 1),
        (SELECT max(x.id) FROM role_profile x WHERE x.code = rp.code AND x.is_active))
      AND (%(q)s::text IS NULL OR rp.title ILIKE %(like)s OR rp.code ILIKE %(like)s)
    ORDER BY d.title, rp.title"""


def listing(conn: psycopg.Connection, q: str | None = None) -> list[dict]:
    q = (q or "").strip() or None
    items = rows(conn, _LIST, {"q": q, "like": f"%{q}%" if q else None})
    for p in items:
        p["state"] = state(p)
    return items


def get(conn: psycopg.Connection, profile_id: int) -> dict:
    p = one(conn, """SELECT rp.*, d.code AS dept_code, d.title AS dept FROM role_profile rp
                     JOIN department d ON d.id = rp.department_id WHERE rp.id = %s""", (profile_id,))
    if not p:
        raise NotFound
    p["state"] = state(p)
    return p


def _validate(changes: dict) -> dict:
    out = {}
    for k, v in changes.items():
        if k not in EDITABLE:
            raise Invalid(f"поле {k} не правится")
        if k == "weights":
            if not isinstance(v, dict) or any(f not in FEATURE_LABELS or not isinstance(w, int) or not 0 <= w <= 100
                                              for f, w in v.items()):
                raise Invalid("веса: известные признаки и целые от 0 до 100")
        elif k in ("must_have", "nice_to_have", "stop_factors"):
            if not isinstance(v, list) or not all(isinstance(r, dict) for r in v):
                raise Invalid(f"{k}: список условий")
        elif k in ("keywords", "donor_companies"):
            if not isinstance(v, list) or not all(isinstance(s, str) and s.strip() for s in v):
                raise Invalid(f"{k}: список строк")
            v = [s.strip() for s in v]
        elif k == "geo" and v not in GEO:
            raise Invalid("география")
        elif k in ("salary_min", "salary_max") and v is not None and (not isinstance(v, int) or v < 0):
            raise Invalid("вилка")
        elif k == "title" and not (isinstance(v, str) and v.strip()):
            raise Invalid("название")
        out[k] = v
    return out


def _next_version(conn, code: str, version: str) -> str:
    m = re.fullmatch(r"(\d+)\.(\d+).*", version or "")
    major, minor = (int(m.group(1)), int(m.group(2))) if m else (1, 0)
    while True:
        minor += 1
        v = f"{major}.{minor}"
        if not val(conn, "SELECT 1 FROM role_profile WHERE code = %s AND version = %s", (code, v)):
            return v


def edit(conn: psycopg.Connection, profile_id: int, changes: dict, user_id: int, now: datetime) -> dict:
    """S26: новая версия с правками, approved_* пусты — выдача по прежней версии."""
    base = get(conn, profile_id)
    changes = _validate(changes)
    if not changes:
        raise Invalid("нет изменений")
    new = {k: changes.get(k, base[k]) for k in EDITABLE}
    version = _next_version(conn, base["code"], base["version"])
    params = {k: (j(v) if k in JSON_FIELDS else v) for k, v in new.items()}
    # имена колонок — из константы EDITABLE, не из запроса; значения — только плейсхолдерами
    nid = val(conn, f"""
        INSERT INTO role_profile (department_id, code, version, valid_from, {", ".join(EDITABLE)},
                                  di_file, di_storage_key, di_revision, di_uploaded_at, di_uploaded_by)
        VALUES (%(dept)s, %(code)s, %(version)s, %(today)s, {", ".join(f"%({k})s" for k in EDITABLE)},
                %(di_file)s, %(di_key)s, %(di_rev)s, %(di_at)s, %(di_by)s) RETURNING id""",  # noqa: S608
              {**params, "dept": base["department_id"], "code": base["code"], "version": version,
               "today": now.date(), "di_file": base["di_file"], "di_key": base["di_storage_key"],
               "di_rev": base["di_revision"], "di_at": None, "di_by": None})
    before = {k: base[k] for k in changes}
    run(conn, """INSERT INTO agent_change (kind, before, after, applied_by, applied_at, note)
                 VALUES ('profile', %s, %s, %s, %s, %s)""",
        (j({"id": profile_id, "version": base["version"], **before}),
         j({"id": nid, "version": version, "state": "pending", **changes}), user_id, now,
         f"правка профиля «{base['title']}», ждёт утверждения"))
    return {"id": nid, "version": version, "state": "pending"}


def approve(conn: psycopg.Connection, profile_id: int, user_id: int, now: datetime) -> dict:
    """S27: версия начинает действовать; пересчёт базы запускает вызывающий (в фоне)."""
    p = get(conn, profile_id)
    if p["approved_at"]:
        raise Conflict("версия уже утверждена")
    if p["state"] == "empty":
        raise Invalid("сначала заполните критерии")
    run(conn, "UPDATE role_profile SET approved_by = %s, approved_at = %s, valid_from = %s WHERE id = %s",
        (user_id, now, now.date(), profile_id))
    run(conn, """INSERT INTO agent_change (kind, before, after, applied_by, applied_at, note)
                 VALUES ('profile', %s, %s, %s, %s, %s)""",
        (j({"id": profile_id, "state": p["state"]}), j({"id": profile_id, "version": p["version"],
                                                          "state": "approved"}),
         user_id, now, f"утверждена версия {p['version']} профиля «{p['title']}»"))
    return {"id": profile_id, "state": "approved"}


def preview(conn: psycopg.Connection, profile_id: int) -> dict:
    """S26: сколько людей пройдёт порог по неутверждённой версии против действующей. В базу не пишет."""
    new = one(conn, """SELECT rp.*, d.code AS dept_code FROM role_profile rp
                       JOIN department d ON d.id = rp.department_id WHERE rp.id = %s""", (profile_id,))
    if not new:
        raise NotFound
    if new["approved_at"]:
        raise Conflict("версия уже утверждена")
    cur = next((p for p in pipeline.current_profiles(conn) if p["code"] == new["code"]), None)
    threshold = int(setting(conn, "watch_threshold", 60))
    limit = int(setting(conn, "preview_max_persons", 5000))
    prm = pipeline.params(conn)
    pn, pc = scoring.Profile.from_row(new), scoring.Profile.from_row(cur) if cur else None
    enter, leave, now_n, new_n = [], [], 0, 0
    ids = [r["id"] for r in rows(conn, "SELECT id FROM person ORDER BY last_event_at DESC NULLS LAST LIMIT %s",
                                 (limit,))]
    for pid in ids:
        person = pipeline.person_data(conn, pid)
        s_new = scoring.score(person, pn, prm).score
        s_now = scoring.score(person, pc, prm).score if pc else 0
        now_n += s_now >= threshold
        new_n += s_new >= threshold
        row = {"person_id": pid, "score_now": s_now, "score_new": s_new}
        if s_new >= threshold > s_now:
            enter.append(row)
        elif s_now >= threshold > s_new:
            leave.append(row)
    labels = {r["id"]: r["label"] for r in rows(conn, "SELECT id, label FROM person WHERE id = ANY(%s)",
                                                ([x["person_id"] for x in enter + leave],))}
    for x in enter + leave:
        x["label"] = labels.get(x["person_id"])
    return {"passes_now": now_n, "passes_new": new_n, "checked": len(ids), "threshold": threshold,
            "enter": enter[:50], "leave": leave[:50]}


def approvers(conn: psycopg.Connection) -> dict:
    """S39: кто может утверждать профили — по role_permission (только чтение до exchange-1c)."""
    roles = [r["role"] for r in rows(conn, """SELECT role::text AS role FROM role_permission
                                              WHERE permission = 'profile.approve' ORDER BY role""")]
    users = rows(conn, """SELECT id, login, full_name, role::text AS role FROM app_user
                          WHERE is_active AND role::text = ANY(%s) ORDER BY full_name""", (roles,))
    return {"roles": roles, "users": users}


# ------------------------------------------------------------------ ДИ

SIGNATURES = {".docx": b"PK\x03\x04", ".pdf": b"%PDF"}


def upload_doc(conn: psycopg.Connection, profile_id: int, file_name: str, data: bytes, revision: str | None,
               user_id: int, now: datetime) -> dict:
    """S28 (пункт 29): файл в хранилище по sha256, запись job_doc, черновая версия профиля «по новой ДИ» —
    это и есть предложение правок в очереди: в работу она пойдёт только после правки и утверждения."""
    base = get(conn, profile_id)
    ext = PurePath(file_name).suffix.lower()
    limit = int(setting(conn, "import_max_file_mb", 20)) * 1024 * 1024
    if ext not in SIGNATURES or not data.startswith(SIGNATURES[ext]):
        raise Invalid("нужен файл .docx или .pdf")
    if len(data) > limit:
        raise Invalid("файл больше допустимого размера")
    digest = hashlib.sha256(data).hexdigest()
    key = f"di/{digest}{ext}"
    path = Path(get_config().storage_dir) / key
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(data)
    doc_id = val(conn, """INSERT INTO job_doc (role_profile_id, file_name, storage_key, revision, sha256, uploaded_by,
                                               uploaded_at) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                 (profile_id, PurePath(file_name).name[:200], key, revision, digest, user_id, now))
    version = _next_version(conn, base["code"], base["version"])
    cols = list(EDITABLE)
    draft_id = val(conn, f"""
        INSERT INTO role_profile (department_id, code, version, valid_from, {", ".join(cols)},
                                  di_file, di_storage_key, di_revision, di_uploaded_at, di_uploaded_by)
        SELECT department_id, code, %s, %s, {", ".join(cols)}, %s, %s, %s, %s, %s
        FROM role_profile WHERE id = %s RETURNING id""",  # noqa: S608 — колонки из константы EDITABLE
                   (version, now.date(), PurePath(file_name).name[:200], key, revision, now, user_id, profile_id))
    run(conn, """INSERT INTO agent_change (kind, before, after, applied_by, applied_at, note)
                 VALUES ('profile', %s, %s, %s, %s, %s)""",
        (j({"id": profile_id, "version": base["version"]}),
         j({"id": draft_id, "version": version, "state": "draft", "job_doc_id": doc_id}), user_id, now,
         f"новая ДИ для «{base['title']}»: черновая версия ждёт правки и утверждения"))
    return {"job_doc_id": doc_id, "draft_version_id": draft_id}
