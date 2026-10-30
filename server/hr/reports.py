"""Журнал изменений системы и недельный отчёт (S32, S33). Отправка письмом — mailer.py (T19b)."""
from __future__ import annotations

from datetime import date, datetime, timedelta

import psycopg

from .db import j, one, rows, val

KIND_TITLE = {"threshold": "Пороги", "weight": "Веса", "profile": "Профили должностей", "template": "Шаблоны",
              "source": "Источники и пределы", "permission": "Права"}


def changelog(conn: psycopg.Connection, date_from: date, date_to: date) -> list[dict]:
    return rows(conn, """
        SELECT c.id, c.kind, c.before, c.after, c.applied_at, c.note, c.proposal_id, u.full_name AS author
        FROM agent_change c LEFT JOIN app_user u ON u.id = c.applied_by
        WHERE c.applied_at >= %s AND c.applied_at < %s + 1 ORDER BY c.applied_at DESC, c.id DESC""",
                (date_from, date_to))


def build_weekly(conn: psycopg.Connection, now: datetime) -> dict:
    """Отчёт за 7 дней по журналу изменений, сгруппированный по видам; «Сформировать сейчас» — тот же вызов."""
    to = now.date()
    frm = to - timedelta(days=6)
    items = changelog(conn, frm, to)
    groups = []
    for kind in sorted({i["kind"] for i in items}):
        sub = [i for i in items if i["kind"] == kind]
        groups.append({"kind": kind, "title": KIND_TITLE.get(kind, kind), "count": len(sub),
                       "items": [{"at": i["applied_at"].isoformat(), "note": i["note"], "author": i["author"]}
                                 for i in sub]})
    body = {"groups": groups}
    rid = val(conn, """INSERT INTO weekly_report (period_from, period_to, changes, body) VALUES (%s, %s, %s, %s)
                       ON CONFLICT (period_from, period_to) DO UPDATE SET changes = EXCLUDED.changes,
                           body = EXCLUDED.body RETURNING id""", (frm, to, len(items), j(body)))
    return latest(conn, rid)


def latest(conn: psycopg.Connection, report_id: int | None = None) -> dict | None:
    return one(conn, """SELECT id, period_from, period_to, changes, recipients, body, sent_at FROM weekly_report
                        WHERE %s::bigint IS NULL OR id = %s ORDER BY period_to DESC, id DESC LIMIT 1""",
               (report_id, report_id))
