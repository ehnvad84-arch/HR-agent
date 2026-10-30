"""Письма руководителю: утренний дайджест (пункт 8), пятничный отчёт (пункт 26), сводка (S41).

В письме — только цифры и ссылка на рабочее место, без имён, контактов и текстов резюме (never.md).
SMTP из .env: порт 465 — SSL, иначе STARTTLS; таймаут 30 с, один повтор через 60 с.
Расписание (08:30, пятница 17:00) — фича deploy; до неё — hr.cli run digest_mail | weekly_report.
"""
from __future__ import annotations

import logging
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage

from . import learning, reports, views
from .clock import SYSTEM, Clock
from .config import get_config
from .db import ConnFactory, run
from .jobs import JobNotConfigured, JobStats

log = logging.getLogger(__name__)
RETRY_AFTER_S = 60


class MailError(RuntimeError):
    pass


def recipients() -> list[str]:
    return list(get_config().mail_to)


def send(subject: str, text: str, clock: Clock | None = None) -> list[str]:
    cfg = get_config()
    to = recipients()
    if not (cfg.smtp_host and to):
        raise JobNotConfigured("почта не настроена: SMTP_HOST и MAIL_TO в .env")
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, cfg.mail_from or cfg.smtp_user, ", ".join(to)
    msg.set_content(text)
    for attempt in (1, 2):
        try:
            cls = smtplib.SMTP_SSL if cfg.smtp_port == 465 else smtplib.SMTP
            kw = {"context": ssl.create_default_context()} if cls is smtplib.SMTP_SSL else {}
            with cls(cfg.smtp_host, cfg.smtp_port, timeout=30, **kw) as s:
                if cls is smtplib.SMTP:
                    s.ehlo()
                    s.starttls(context=ssl.create_default_context())
                if cfg.smtp_user:
                    s.login(cfg.smtp_user, cfg.smtp_password)
                s.send_message(msg)
            return to
        except (OSError, smtplib.SMTPException) as e:
            log.warning("письмо не ушло: %s", type(e).__name__)
            if attempt == 1:
                (clock or SYSTEM).sleep(RETRY_AFTER_S)
    raise MailError("письмо не отправлено: почтовый сервер не ответил")


def _link() -> str:
    return get_config().web_url


def digest_text(conn, now: datetime) -> str:
    t = views.today(conn, now)
    return (f"Доброе утро. На сегодня в рабочем месте:\n"
            f"— сигналов «обратите внимание»: {len(t['signals'])}\n"
            f"— действий ждут подтверждения: {len(t['actions'])}\n"
            f"— кандидатов зависли: {len(t['stalled'])}\n"
            f"— встреч сегодня и завтра: {len(t['meetings'])}\n\n"
            f"Открыть: {_link()}\n")


def digest_mail(conn_factory: ConnFactory, *, clock: Clock | None = None, **_) -> JobStats:
    """Задача digest_mail (08:30)."""
    clock = clock or SYSTEM
    stats = JobStats()
    if not (get_config().smtp_host and recipients()):
        stats.note = "почта не настроена: SMTP_HOST и MAIL_TO"
        return stats
    with conn_factory() as conn:
        text = digest_text(conn, clock.now())
    send("Подбор: утренний дайджест", text, clock)
    stats.changed = 1
    return stats


def report_text(rep: dict) -> str:
    lines = [f"Изменения в работе агента за {rep['period_from']} — {rep['period_to']}: {rep['changes']}."]
    for g in rep["body"].get("groups", []):
        lines.append(f"— {g['title']}: {g['count']}")
    lines.append(f"\nПодробно — «Журнал и отчёт»: {_link()}")
    return "\n".join(lines)


def send_weekly(conn_factory: ConnFactory, clock: Clock | None = None) -> dict:
    """Отчёт собирается и сохраняется до отправки: отказ почты его не теряет."""
    clock = clock or SYSTEM
    with conn_factory() as conn:
        rep = reports.build_weekly(conn, clock.now())
    to = send("Подбор: изменения за неделю", report_text(rep), clock)
    with conn_factory() as conn:
        run(conn, "UPDATE weekly_report SET sent_at = %s, recipients = %s WHERE id = %s", (clock.now(), to, rep["id"]))
        return reports.latest(conn, rep["id"])


def weekly_report(conn_factory: ConnFactory, *, clock: Clock | None = None, **_) -> JobStats:
    """Задача weekly_report (пятница 17:00)."""
    stats = JobStats()
    if not (get_config().smtp_host and recipients()):
        stats.note = "почта не настроена: SMTP_HOST и MAIL_TO"
        return stats
    send_weekly(conn_factory, clock)
    stats.changed = 1
    return stats


def send_digest_summary(conn_factory: ConnFactory, clock: Clock | None = None) -> list[str]:
    """S41: «Отправить на почту» у недельной сводки — сколько предложений и сколько ждёт решения."""
    with conn_factory() as conn:
        d = learning.current(conn) or {"proposals": [], "messages": 0}
    pending = sum(1 for p in d["proposals"] if p["decision"] == "pending")
    text = (f"Недельная сводка агента: {len(d['proposals'])} предлож., ждут решения: {pending}, "
            f"сообщений в чате: {d.get('messages', 0)}.\n\nРешить: {_link()}\n")
    return send("Подбор: недельная сводка", text, clock)
