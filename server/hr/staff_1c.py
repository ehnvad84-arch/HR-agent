"""Выгрузка сотрудников из 1С (manager-cabinet S1, S18): кто есть кто в портале. Только чтение (AD-4).

Файл `employees_*.json` в обменном каталоге:
{"kind": "employees", "period": "YYYY-MM-DD",
 "employees": [{"id", "login", "full_name", "department_id" (= id подразделения в отчёте оргструктуры),
                "position", "active", "email"?}]}
Роль — по правилу: отдел `hr_org_unit_title` и вложенные — специалист (со словом из `manager_title_words` в
должности — руководитель отдела персонала); корень и «директор» — директор; слово из списка — руководитель
подразделения; прочие — без доступа. Роль из ops.user_override не трогается. Пропавший из файла или
active = false теряет доступ, его сессии и коды гасятся; переданные им люди остаются у отдела персонала.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

import psycopg

from .db import j, one, rows, run, setting, val

MAX_BYTES = 5 * 1024 * 1024
PREFIX = "employees"


class Invalid(ValueError):
    pass


class NotFound(LookupError):
    pass


def latest_file(directory: str) -> Path:
    d = Path(directory)
    files = sorted((f for f in d.glob(f"{PREFIX}*.json") if f.is_file()), key=lambda f: f.stat().st_mtime) \
        if d.is_dir() else []
    if not files:
        raise NotFound("в обменном каталоге нет выгрузки сотрудников")
    return files[-1]


def _parse(data: bytes) -> tuple[dict, list[dict]]:
    try:
        report = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise Invalid("файл не читается как JSON") from None
    emps = report.get("employees") if isinstance(report, dict) else None
    if not isinstance(emps, list) or not emps:
        raise Invalid("это не выгрузка сотрудников: нет списка employees")
    out, logins, ids = [], set(), set()
    for e in emps:
        if not isinstance(e, dict):
            raise Invalid("строка сотрудника — не объект")
        ext, login, name = (str(e.get(k) or "").strip() for k in ("id", "login", "full_name"))
        if not ext or not login or not name:
            raise Invalid("сотрудник без id, логина или ФИО")
        login = login.lower()
        if login in logins or ext in ids:
            raise Invalid(f"логин или id встречается дважды: {login}")
        logins.add(login)
        ids.add(ext)
        out.append({"ext": ext, "login": login, "full_name": re.sub(r"\s+", " ", name),
                    "unit": str(e.get("department_id") or "").strip() or None,
                    "position": (str(e.get("position") or "").strip() or None),
                    "active": e.get("active", True) is not False,
                    "email": (str(e.get("email") or "").strip().lower() or None)})
    return report, out


def _role_rule(conn, unit_id: int | None, position: str | None, hr_units: set[int], roots: set[int],
               words: list[str]) -> str:
    pos = (position or "").lower()
    boss = any(w in pos for w in words)
    if unit_id in hr_units:
        return "head_recruiter" if boss else "recruiter"
    if unit_id in roots and "директор" in pos:
        return "director"
    return "hiring_manager" if boss else "no_access"


def apply(conn: psycopg.Connection, data: bytes, file_name: str, user_id: int | None, now: datetime) -> dict:
    if len(data) > MAX_BYTES:
        raise Invalid("файл больше 5 МБ")
    sha = hashlib.sha256(data).hexdigest()
    done = one(conn, "SELECT id, state FROM ops.user_sync WHERE sha256 = %s", (sha,))
    if done and done["state"] == "applied":
        return {"state": "same", "file_name": file_name}
    report, emps = _parse(data)

    active_ext = {r["external_id"] for r in rows(conn, """SELECT external_id FROM app_user
                                                          WHERE is_active AND external_id IS NOT NULL
                                                            AND role::text <> 'agent'""")}
    gone = active_ext - {e["ext"] for e in emps if e["active"]}
    pct = float(setting(conn, "users_max_drop_pct", 20))
    if active_ext and len(gone) * 100.0 / len(active_ext) > pct:
        raise Invalid(f"из выгрузки пропало {len(gone)} из {len(active_ext)} сотрудников — больше {pct:g}%; "
                      "похоже на неполный файл, ничего не применено")

    units = {r["external_id"]: r["id"] for r in rows(conn, "SELECT id, external_id FROM org_unit")}
    roots = {r["id"] for r in rows(conn, "SELECT id FROM org_unit WHERE parent_id IS NULL AND is_active")}
    hr_title = str(setting(conn, "hr_org_unit_title", "Отдел управления персоналом"))
    hr_units = {r["id"] for r in rows(conn, """
        WITH RECURSIVE t AS (SELECT id FROM org_unit WHERE title = %s
                             UNION SELECT u.id FROM org_unit u JOIN t ON u.parent_id = t.id) SELECT id FROM t""",
                                      (hr_title,))}
    words = [w.lower() for w in (setting(conn, "manager_title_words", ["руководитель", "начальник", "директор"])
                                 or [])]
    overrides = {r["user_id"]: r["role"]
                 for r in rows(conn, "SELECT user_id, role::text AS role FROM ops.user_override")}

    new = changed = fired = 0
    log: list[dict] = []
    for e in emps:
        unit_id = units.get(e["unit"]) if e["unit"] else None
        old = one(conn, """SELECT id, role::text AS role, org_unit_id, is_active, full_name, position, email
                           FROM app_user WHERE external_id = %s""", (e["ext"],)) or \
            one(conn, """SELECT id, role::text AS role, org_unit_id, is_active, full_name, position, email
                         FROM app_user WHERE lower(login) = %s AND external_id IS NULL""", (e["login"],))
        if old and old["role"] == "agent":
            continue
        role = overrides.get(old["id"]) if old else None
        role = role or _role_rule(conn, unit_id, e["position"], hr_units, roots, words)
        if not old:
            if not e["active"]:
                continue
            if val(conn, "SELECT 1 FROM app_user WHERE lower(login) = %s", (e["login"],)):
                raise Invalid(f"логин {e['login']} уже занят другим сотрудником")
            run(conn, """INSERT INTO app_user (external_id, login, full_name, email, position, org_unit_id, role,
                                               is_active, synced_at)
                         VALUES (%s, %s, %s, %s, %s, %s, %s, true, %s)""",
                (e["ext"], e["login"], e["full_name"], e["email"], e["position"], unit_id, role, now))
            new += 1
            log.append({"login": e["login"], "role": role, "kind": "new"})
            continue
        diff = (old["role"] != role or old["org_unit_id"] != unit_id or old["is_active"] != e["active"]
                or old["full_name"] != e["full_name"] or old["position"] != e["position"])
        run(conn, """UPDATE app_user SET external_id = %s, full_name = %s, email = coalesce(%s, email), position = %s,
                         org_unit_id = %s, role = %s, is_active = %s, synced_at = %s WHERE id = %s""",
            (e["ext"], e["full_name"], e["email"], e["position"], unit_id, role, e["active"], now, old["id"]))
        if old["is_active"] and not e["active"]:
            _revoke(conn, old["id"], now)
            fired += 1
            log.append({"login": e["login"], "kind": "fired"})
        elif diff:
            changed += 1
            if old["role"] != role:
                _revoke(conn, old["id"], now)   # права сменились — войти заново
            log.append({"login": e["login"], "role": role, "kind": "changed"})
    for ext in gone:
        uid = val(conn, "SELECT id FROM app_user WHERE external_id = %s", (ext,))
        if uid and not any(e["ext"] == ext for e in emps):
            run(conn, "UPDATE app_user SET is_active = false, synced_at = %s WHERE id = %s", (now, uid))
            _revoke(conn, uid, now)
            fired += 1
            log.append({"external_id": ext, "kind": "fired"})

    exported = now
    if isinstance(report.get("period"), str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", report["period"]):
        exported = datetime.fromisoformat(report["period"] + "T00:00:00+03:00")
    if done:
        run(conn, """UPDATE ops.user_sync SET state = 'applied', error = NULL, received_at = %s, users = %s,
                         changes = %s WHERE id = %s""", (now, len(emps), new + changed + fired, done["id"]))
    else:
        run(conn, """INSERT INTO ops.user_sync (file_name, sha256, exported_at, received_at, state, users, changes)
                     VALUES (%s, %s, %s, %s, 'applied', %s, %s)""",
            (file_name, sha, exported, now, len(emps), new + changed + fired))
    if log:
        run(conn, """INSERT INTO agent_change (kind, before, after, applied_by, applied_at, note)
                     VALUES ('access', NULL, %s, %s, %s, %s)""",
            (j({"changes": log[:500]}), user_id, now,
             f"выгрузка сотрудников 1С: новых {new}, изменено {changed}, уволено {fired}"))
    return {"state": "applied", "file_name": file_name, "new": new, "changed": changed, "fired": fired}


def _revoke(conn, user_id: int, now: datetime) -> None:
    run(conn, "UPDATE ops.web_session SET revoked_at = %s WHERE user_id = %s AND revoked_at IS NULL", (now, user_id))
    run(conn, """UPDATE ops.invite_code SET revoked_at = %s
                 WHERE user_id = %s AND used_at IS NULL AND revoked_at IS NULL""", (now, user_id))


def reject(conn: psycopg.Connection, data: bytes, file_name: str, error: str, now: datetime) -> None:
    sha = hashlib.sha256(data).hexdigest()
    run(conn, """INSERT INTO ops.user_sync (file_name, sha256, exported_at, received_at, state, error)
                 VALUES (%s, %s, %s, %s, 'rejected', %s)
                 ON CONFLICT (sha256) DO UPDATE SET state = 'rejected', error = EXCLUDED.error,
                     received_at = EXCLUDED.received_at
                 WHERE ops.user_sync.state <> 'applied'""", (file_name, sha, now, now, error[:500]))
