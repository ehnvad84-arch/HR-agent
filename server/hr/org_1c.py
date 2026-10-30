"""Оргструктура из 1С (S47, раздел 7.2б ТЗ): отчёт «Организационная структура» на чтение, встречной записи нет.

Файл — вложенный JSON: подразделение → должности и дочерние подразделения. Сотрудники в отчёте есть, но
не читаются и не хранятся (ни ФИО, ни табельные номера, ни даты рождения) — только число ставок.
Снимок полный: что пропало из файла, помечается закрытым, ничего не удаляется, каждое отличие — в org_change.
Должность 1С = название без хвоста «/Отдел…/»; у каждой — портрет: найденный по названию или пустая заготовка,
которая не идёт в подбор, пока её не заполнят и не утвердят.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import psycopg

from .db import j, one, rows, run, setting, val

MAX_BYTES = 5 * 1024 * 1024
# из позиции в базу идут только эти поля; employees и всё прочее отбрасывается
POSITION_FIELDS = ("id", "name", "order", "rates", "occupied", "vacant")


class Invalid(ValueError):
    pass


class NotFound(LookupError):
    pass


@dataclass
class Unit:
    ext: str
    parent: str | None
    title: str
    order: int | None


@dataclass
class Position:
    ext: str
    unit: str
    title_1c: str
    rates: float
    occupied: float
    raw: dict


def job_title(title_1c: str) -> str:
    """«Менеджер проектных продаж /Отдел продаж/» → «Менеджер проектных продаж»."""
    t = re.sub(r"\s*/[^/]*/\s*$", "", title_1c or "")
    return re.sub(r"\s+", " ", t).strip()


def job_key(title_1c: str) -> str:
    return "title:" + job_title(title_1c).lower().replace("ё", "е")


def _num(v, what: str) -> float:
    if isinstance(v, bool) or not isinstance(v, int | float):
        raise Invalid(f"{what}: не число")
    return float(v)


def parse(data: dict) -> tuple[list[Unit], list[Position]]:
    """Подразделения в порядке обхода (родитель раньше детей) и позиции. Проверяет форму отчёта."""
    if not isinstance(data, dict) or not isinstance(data.get("departments"), list) or not data["departments"]:
        raise Invalid("это не отчёт 1С «Организационная структура»: нет списка departments")
    units: list[Unit] = []
    positions: list[Position] = []
    seen_u: set[str] = set()
    seen_p: set[str] = set()

    def walk(dep: dict, parent: str | None) -> None:
        ext = str(dep.get("id") or "").strip()
        title = re.sub(r"\s+", " ", str(dep.get("name") or "")).strip()
        if not ext or not title:
            raise Invalid("подразделение без id или названия")
        if ext in seen_u:
            raise Invalid(f"подразделение «{title}» встречается дважды")
        seen_u.add(ext)
        order = dep.get("order")
        units.append(Unit(ext, parent, title, order if isinstance(order, int) else None))
        for p in dep.get("positions") or []:
            pid = str(p.get("id") or "").strip()
            name = str(p.get("name") or "").strip()
            if not pid or not job_title(name):
                raise Invalid(f"в подразделении «{title}» должность без id или названия")
            if pid in seen_p:
                raise Invalid(f"должность «{name}» встречается дважды")
            seen_p.add(pid)
            rates = _num(p.get("rates"), f"ставки «{name}»")
            occupied = _num(p.get("occupied", 0), f"занятые ставки «{name}»")
            if rates < 0:
                raise Invalid(f"ставки «{name}» меньше нуля")
            raw = {k: p[k] for k in POSITION_FIELDS if k in p}
            positions.append(Position(pid, ext, name, rates, max(occupied, 0.0), raw))
        for child in dep.get("departments") or []:
            walk(child, ext)

    for top in data["departments"]:
        walk(top, None)
    return units, positions


def _dept_for(units: list[Unit]) -> dict[str, str]:
    """Функциональная группа подразделения — его верхнее подразделение под корнем (корень — сам себе)."""
    by = {u.ext: u for u in units}
    out = {}
    for u in units:
        cur = u
        while cur.parent and by[cur.parent].parent:
            cur = by[cur.parent]
        out[u.ext] = cur.ext
    return out


def _department_id(conn, unit: Unit) -> int:
    code = "1c-" + unit.ext[:8]
    did = val(conn, "SELECT id FROM department WHERE code = %s", (code,))
    if did is None:
        did = val(conn, "INSERT INTO department (code, title) VALUES (%s, %s) RETURNING id", (code, unit.title))
    return did


def apply(conn: psycopg.Connection, data: bytes, file_name: str, user_id: int | None, now: datetime) -> dict:
    """Применяет выгрузку целиком в одной транзакции вызывающего. Тот же файл второй раз — «same»."""
    if len(data) > MAX_BYTES:
        raise Invalid("файл больше 5 МБ")
    sha = hashlib.sha256(data).hexdigest()
    done = one(conn, "SELECT state FROM org_sync WHERE sha256 = %s", (sha,))
    if done and done["state"] == "applied":
        return {"state": "same", "file_name": file_name}
    try:
        report = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise Invalid("файл не читается как JSON") from None
    units, positions = parse(report)

    active_before = val(conn, "SELECT count(*) FROM staff_position WHERE is_active")
    gone = val(conn, "SELECT count(*) FROM staff_position WHERE is_active AND NOT (external_id = ANY(%s))",
               ([p.ext for p in positions],))
    max_drop = float(setting(conn, "org_max_drop_pct", 20))
    if active_before and gone * 100.0 / active_before > max_drop:
        raise Invalid(f"из выгрузки пропало {gone} из {active_before} должностей — больше {max_drop:g}%; "
                      "похоже на неполный файл, ничего не применено")

    exported = now
    if isinstance(report.get("period"), str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", report["period"]):
        exported = datetime.fromisoformat(report["period"] + "T00:00:00+03:00")
    if done:   # был отклонён — пробуем снова той же строкой
        sync_id = val(conn, "SELECT id FROM org_sync WHERE sha256 = %s", (sha,))
        run(conn, "UPDATE org_sync SET state = 'applied', error = NULL, received_at = %s WHERE id = %s",
            (now, sync_id))
    else:
        sync_id = val(conn, """INSERT INTO org_sync (file_name, sha256, exported_at, received_at, state)
                               VALUES (%s, %s, %s, %s, 'applied') RETURNING id""", (file_name, sha, exported, now))
    changes = 0

    def change(kind, unit_id=None, pos_id=None, before=None, after=None):
        nonlocal changes
        changes += 1
        run(conn, """INSERT INTO org_change (sync_id, kind, org_unit_id, staff_position_id, before, after)
                     VALUES (%s, %s, %s, %s, %s, %s)""",
            (sync_id, kind, unit_id, pos_id, j(before) if before is not None else None,
             j(after) if after is not None else None))

    organization = report.get("organization") if isinstance(report.get("organization"), str) else None
    ids: dict[str, int] = {}
    for u in units:
        parent_id = ids.get(u.parent) if u.parent else None
        old = one(conn, "SELECT id, parent_id, title, is_active FROM org_unit WHERE external_id = %s", (u.ext,))
        if not old:
            uid = val(conn, """INSERT INTO org_unit (external_id, parent_id, organization, title, sort_order, synced_at)
                               VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
                      (u.ext, parent_id, organization, u.title, u.order, now))
            change("unit_added", uid, after={"title": u.title})
        else:
            uid = old["id"]
            if not old["is_active"]:
                change("unit_reopened", uid)
            if old["title"] != u.title:
                change("unit_renamed", uid, before={"title": old["title"]}, after={"title": u.title})
            if old["parent_id"] != parent_id:
                change("unit_moved", uid, before={"parent_id": old["parent_id"]}, after={"parent_id": parent_id})
            run(conn, """UPDATE org_unit SET parent_id = %s, organization = %s, title = %s, sort_order = %s,
                                is_active = true, synced_at = %s WHERE id = %s""",
                (parent_id, organization, u.title, u.order, now, uid))
        ids[u.ext] = uid
    for r in rows(conn, "SELECT id, title FROM org_unit WHERE is_active AND NOT (external_id = ANY(%s))",
                  (list(ids),)):
        run(conn, "UPDATE org_unit SET is_active = false, synced_at = %s WHERE id = %s", (now, r["id"]))
        change("unit_closed", r["id"], before={"title": r["title"]})

    # функциональные группы: верхние подразделения; ставим, только если человек ещё не выбрал свою
    by = {u.ext: u for u in units}
    top = _dept_for(units)
    dept_ids = {ext: _department_id(conn, by[ext]) for ext in set(top.values())}
    for u in units:
        if u.ext == top[u.ext]:
            run(conn, "UPDATE org_unit SET department_id = coalesce(department_id, %s) WHERE id = %s",
                (dept_ids[u.ext], ids[u.ext]))

    for p in positions:
        uid = ids[p.unit]
        key = job_key(p.title_1c)
        old = one(conn, """SELECT id, org_unit_id, rates_total, rates_occupied, is_active
                           FROM staff_position WHERE external_id = %s""", (p.ext,))
        if not old:
            pid = val(conn, """INSERT INTO staff_position (external_id, org_unit_id, job_external_id, job_title,
                                   rates_total, rates_occupied, raw, synced_at)
                               VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                      (p.ext, uid, key, p.title_1c, p.rates, p.occupied, j(p.raw), now))
            change("position_added", uid, pid, after={"title": p.title_1c, "rates": p.rates})
            continue
        pid = old["id"]
        if not old["is_active"]:
            change("position_reopened", uid, pid)
        if old["org_unit_id"] != uid:
            change("position_moved", uid, pid, before={"org_unit_id": old["org_unit_id"]}, after={"org_unit_id": uid})
        if float(old["rates_total"]) != p.rates:
            change("rates_changed", uid, pid, before={"rates": float(old["rates_total"])}, after={"rates": p.rates})
        if float(old["rates_occupied"]) != p.occupied:
            change("occupancy_changed", uid, pid, before={"occupied": float(old["rates_occupied"])},
                   after={"occupied": p.occupied})
        run(conn, """UPDATE staff_position SET org_unit_id = %s, job_external_id = %s, job_title = %s,
                            rates_total = %s, rates_occupied = %s, raw = %s, is_active = true, synced_at = %s
                     WHERE id = %s""", (uid, key, p.title_1c, p.rates, p.occupied, j(p.raw), now, pid))
    for r in rows(conn, """SELECT id, org_unit_id, job_title FROM staff_position
                           WHERE is_active AND NOT (external_id = ANY(%s))""", ([p.ext for p in positions],)):
        run(conn, "UPDATE staff_position SET is_active = false, synced_at = %s WHERE id = %s", (now, r["id"]))
        change("position_closed", r["org_unit_id"], r["id"], before={"title": r["job_title"]})

    new_profiles = _map_jobs(conn, positions, top, dept_ids, user_id, now)
    run(conn, "UPDATE org_sync SET units = %s, positions = %s, changes = %s WHERE id = %s",
        (len(units), len(positions), changes, sync_id))
    return {"state": "applied", "file_name": file_name, "units": len(units), "positions": len(positions),
            "changes": changes, "new_profiles": new_profiles}


def _map_jobs(conn, positions: list[Position], top: dict[str, str], dept_ids: dict[str, int],
              user_id: int | None, now: datetime) -> int:
    """Должности без сопоставления: портрет с тем же названием или пустая заготовка «критерии не заполнены»."""
    first: dict[str, Position] = {}
    for p in positions:
        first.setdefault(job_key(p.title_1c), p)
    created = []
    for key, p in first.items():
        if val(conn, "SELECT 1 FROM job_role_map WHERE job_external_id = %s", (key,)):
            continue
        title = job_title(p.title_1c)
        code = val(conn, """SELECT code FROM role_profile WHERE is_active
                              AND replace(lower(title), 'ё', 'е') = %s ORDER BY id LIMIT 1""",
                   (key.removeprefix("title:"),))
        if code is None:
            code = "1c-" + hashlib.sha256(key.encode()).hexdigest()[:10]
            if not val(conn, "SELECT 1 FROM role_profile WHERE code = %s", (code,)):
                run(conn, """INSERT INTO role_profile (department_id, code, title, version, valid_from)
                             VALUES (%s, %s, %s, '0.1', %s)""", (dept_ids[top[p.unit]], code, title, now.date()))
                created.append(title)
        run(conn, """INSERT INTO job_role_map (job_external_id, job_title, role_code, mapped_by, mapped_at)
                     VALUES (%s, %s, %s, %s, %s)""", (key, title, code, user_id, now))
    if created:
        run(conn, """INSERT INTO agent_change (kind, before, after, applied_by, applied_at, note)
                     VALUES ('profile', NULL, %s, %s, %s, %s)""",
            (j({"created": len(created)}), user_id, now,
             f"из оргструктуры 1С заведено пустых портретов: {len(created)}; в подбор не идут до заполнения "
             "и утверждения"))
    return len(created)


def reject(conn: psycopg.Connection, data: bytes, file_name: str, error: str, now: datetime) -> None:
    """Отклонённая выгрузка остаётся в org_sync с причиной — данные не тронуты."""
    sha = hashlib.sha256(data).hexdigest()
    run(conn, """INSERT INTO org_sync (file_name, sha256, exported_at, received_at, state, error)
                 VALUES (%s, %s, %s, %s, 'rejected', %s)
                 ON CONFLICT (sha256) DO UPDATE SET state = 'rejected', error = EXCLUDED.error,
                     received_at = EXCLUDED.received_at
                 WHERE org_sync.state <> 'applied'""", (file_name, sha, now, now, error[:500]))


def latest_file(directory: str) -> Path:
    """Самый свежий .json в обменном каталоге 1С."""
    d = Path(directory)
    files = sorted((f for f in d.glob("*.json") if f.is_file() and not f.name.startswith("employees")),
                   key=lambda f: f.stat().st_mtime) if d.is_dir() else []   # employees_* — выгрузка сотрудников
    if not files:
        raise NotFound("в обменном каталоге нет выгрузки 1С")
    return files[-1]


def tree(conn: psycopg.Connection, profiles_by_code: dict[str, dict]) -> dict:
    """GET /api/org: последняя выгрузка и плоский список активных подразделений с должностями."""
    sync = one(conn, """SELECT received_at, file_name, units, positions FROM org_sync WHERE state = 'applied'
                        ORDER BY received_at DESC, id DESC LIMIT 1""")
    units = rows(conn, """SELECT id, parent_id, title, sort_order AS "order" FROM org_unit WHERE is_active
                          ORDER BY sort_order NULLS LAST, title""")
    pos = rows(conn, """SELECT sp.id, sp.org_unit_id, sp.job_title AS title_1c, sp.rates_total, sp.rates_occupied,
                               sp.rates_open, m.role_code
                        FROM staff_position sp LEFT JOIN job_role_map m ON m.job_external_id = sp.job_external_id
                        WHERE sp.is_active
                        ORDER BY (sp.raw->>'order')::int NULLS LAST, sp.job_title""")
    by_unit: dict[int, list] = {}
    for p in pos:
        prof = profiles_by_code.get(p.pop("role_code") or "")
        by_unit.setdefault(p.pop("org_unit_id"), []).append({
            **p, "title": job_title(p["title_1c"]),
            "rates_total": float(p["rates_total"]), "rates_occupied": float(p["rates_occupied"]),
            "rates_open": float(p["rates_open"]),
            "profile": {k: prof.get(k) for k in ("id", "code", "title", "state", "version", "geo", "pending_id")}
            if prof else None})
    for u in units:
        u["positions"] = by_unit.get(u["id"], [])
    return {"sync": sync, "units": units}
