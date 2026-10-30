"""Проверка загрузок резюме (manager-cabinet S20): итог каждого прогона — всем сотрудникам отдела персонала.

После каждого прогона загрузки (кнопкой или по расписанию) — сообщение «прошла: просмотрено, новых,
изменилось» или тревога «не прошла — требуется разбирательство» с причиной простыми словами. Раз в час
задача load_watchdog ищет загрузки, которые не начались по расписанию или зависли. Одна и та же тревога
не повторяется чаще load_alarm_repeat_hours. Сообщения — в notification каждому действующему HR и
письмом на MAIL_TO, если почта настроена. Только цифры и причины — без данных о людях.
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg

from .clock import SYSTEM, Clock
from .db import ConnFactory, j, one, rows, run, setting
from .jobs import JobBlocked, JobLimit, JobNotConfigured, JobResult, JobStats

log = logging.getLogger(__name__)
MSK = ZoneInfo("Europe/Moscow")
LOADS = {"collect_hh": "Загрузка резюме hh", "collect_hh_daily": "Загрузка резюме hh в 18:00",
         "collect_trudvsem": "Загрузка «Работы России»", "photos": "Загрузка фото"}
# расписание (install_autostart.sh): задача, дни недели (0 — понедельник) или None — каждый день, час:минута МСК
SCHEDULE = {"collect_hh_daily": (None, 18, 0), "collect_trudvsem": ({1}, 1, 0)}
HR_ROLES = ["recruiter", "head_recruiter"]


def _reason(res: JobResult) -> str:
    e = res.exc
    if isinstance(e, JobBlocked):
        return f"площадка показала проверку на робота или ограничила частоту — сбор остановлен ({res.error})"
    if isinstance(e, JobNotConfigured):
        return f"не настроено: {res.error}"
    if isinstance(e, JobLimit):
        return f"лимит площадки: {res.error}"
    return f"сбой: {res.error or 'неизвестная ошибка'} — посмотрите журнал задачи на «Схеме»"


def judge(conn: psycopg.Connection, job: str, res: JobResult) -> tuple[str, str]:
    """(severity ok | alarm, текст)."""
    st = res.stats
    if not res.ok:
        if res.error == "уже идёт":
            return "ok", "не запущена: такая загрузка уже идёт"
        return "alarm", _reason(res)
    if st.note and "лимит" in st.note and not st.seen:
        return "ok", f"дневной лимит на сегодня уже выбран — {st.note}"
    pct = float(setting(conn, "load_alarm_error_pct", 20))
    if st.seen and st.errors * 100.0 / st.seen > pct:
        return "alarm", f"много ошибок: не принято {st.errors} из {st.seen} резюме"
    if not st.seen and job != "photos":
        return "alarm", "пришло 0 резюме — обычно так не бывает, проверьте площадку и ключи"
    text = f"просмотрено {st.seen}, новых {st.new}, изменилось {st.changed}"
    if st.note:
        text += f". {st.note}"
    return "ok", text


def notify(conn: psycopg.Connection, job: str, severity: str, text: str, now: datetime, run_id: int | None = None,
           send_mail: bool = True) -> int:
    """Сообщение каждому действующему HR; тревога с той же причиной не чаще load_alarm_repeat_hours."""
    title = f"{LOADS.get(job, job)}: " + ("прошла" if severity == "ok" else "не прошла — требуется разбирательство")
    if severity == "alarm":
        hours = float(setting(conn, "load_alarm_repeat_hours", 6))
        if one(conn, """SELECT 1 FROM notification WHERE kind = 'load_alarm' AND body->>'job' = %s
                        AND body->>'text' = %s AND created_at > %s LIMIT 1""",
               (job, text, now - timedelta(hours=hours))):
            return 0
    body = {"title": title, "text": text, "job": job, "severity": severity, "run_id": run_id,
            "event": secrets.token_hex(8)}
    users = [r["id"] for r in rows(conn, """SELECT id FROM app_user WHERE is_active AND role::text = ANY(%s)""",
                                   (HR_ROLES,))]
    for uid in users:
        run(conn, """INSERT INTO notification (user_id, kind, body, created_at) VALUES (%s, %s, %s, %s)""",
            (uid, f"load_{severity}", j(body), now))
    if send_mail:
        from . import mailer
        try:
            mailer.send(title, f"{title}\n\n{text}\n\n{mailer._link()}")
            run(conn, "UPDATE notification SET sent_email = true WHERE body->>'event' = %s", (body["event"],))
        except Exception as e:  # noqa: BLE001 — почта не настроена или недоступна: сообщение в портале уже есть
            log.info("письмо о загрузке не ушло: %s", type(e).__name__)
    return len(users)


def after_run(factory: ConnFactory, job: str, res: JobResult, run_id: int | None, now: datetime | None = None) -> None:
    """Вызывается jobs.run после каждого прогона загрузки. Сбой проверки не роняет саму загрузку."""
    if job not in LOADS:
        return
    try:
        with factory() as conn:
            severity, text = judge(conn, job, res)
            notify(conn, job, severity, text, now or SYSTEM.now(), run_id)
    except Exception:  # noqa: BLE001
        log.exception("проверка загрузки %s не выполнена", job)


def load_watchdog(conn_factory: ConnFactory, *, clock: Clock | None = None, **_) -> JobStats:
    """Задача load_watchdog (раз в час): загрузка не началась по расписанию или зависла — тревога."""
    now = (clock or SYSTEM).now()
    stats = JobStats()
    local = now.astimezone(MSK)
    with conn_factory() as conn:
        stuck_h = float(setting(conn, "load_stuck_hours", 3))
        for r in rows(conn, """SELECT id, job, started_at FROM ops.job_run
                               WHERE finished_at IS NULL AND job = ANY(%s) AND started_at < %s""",
                      (list(LOADS), now - timedelta(hours=stuck_h))):
            stats.alerts += bool(notify(conn, r["job"], "alarm",
                                        f"идёт дольше {stuck_h:g} ч — похоже, зависла (начата "
                                        f"{r['started_at'].astimezone(MSK):%d.%m %H:%M})", now, r["id"]))
        for job, (days, hh, mm) in SCHEDULE.items():
            due = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if (days is not None and local.weekday() not in days) or local < due + timedelta(hours=1):
                continue
            started = one(conn, "SELECT 1 FROM ops.job_run WHERE job = %s AND started_at >= %s LIMIT 1",
                          (job, due - timedelta(minutes=10)))
            if not started:
                stats.alerts += bool(notify(conn, job, "alarm",
                                            f"не началась по расписанию ({due:%d.%m %H:%M} МСК) — проверьте, "
                                            "работают ли сервер и таймер", now))
        stats.seen = 1
    return stats


def listing(conn: psycopg.Connection, user_id: int, limit: int = 50) -> dict:
    items = rows(conn, """SELECT id, kind, body, created_at, read_at FROM notification
                          WHERE user_id = %s AND kind LIKE 'load_%%' ORDER BY created_at DESC LIMIT %s""",
                 (user_id, limit))
    for n in items:
        n["ack"] = n["body"].pop("ack", None)
    alarm = next((n for n in items if n["kind"] == "load_alarm" and not n["ack"]), None)
    return {"items": items, "unread": sum(1 for n in items if not n["read_at"]),
            "alarm": {k: alarm[k] for k in ("id", "created_at")} | {"title": alarm["body"]["title"],
                                                                    "text": alarm["body"]["text"]} if alarm else None}


def read(conn: psycopg.Connection, user_id: int, nid: int, now: datetime) -> bool:
    return bool(run(conn, "UPDATE notification SET read_at = coalesce(read_at, %s) WHERE id = %s AND user_id = %s",
                    (now, nid, user_id)))


def ack(conn: psycopg.Connection, user_id: int, full_name: str, nid: int, now: datetime) -> bool:
    """«Разбираюсь»: имя видно у всех HR, полоса тревоги снимается у всех."""
    ev = one(conn, "SELECT body->>'event' AS e FROM notification WHERE id = %s AND user_id = %s", (nid, user_id))
    if not ev:
        return False
    run(conn, """UPDATE notification SET body = body || %s, read_at = coalesce(read_at, %s)
                 WHERE body->>'event' = %s""", (j({"ack": {"by": full_name, "at": now.isoformat()}}), now, ev["e"]))
    return True
