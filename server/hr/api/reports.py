"""Журнал изменений и недельный отчёт (S32, S33)."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import auth, mailer, reports
from ..clock import Clock
from ..db import ConnFactory
from ..deps import get_clock, get_conn_factory
from ..jobs import JobNotConfigured

router = APIRouter()
Reader = Annotated[auth.User, Depends(auth.require("report.read"))]
Conn = Annotated[ConnFactory, Depends(get_conn_factory)]
Now = Annotated[Clock, Depends(get_clock)]


@router.get("/api/changelog")
def changelog(user: Reader, conn_factory: Conn, clock: Now,
              date_from: Annotated[date | None, Query(alias="from")] = None,
              date_to: Annotated[date | None, Query(alias="to")] = None):
    to = date_to or clock.now().date()
    with conn_factory() as conn:
        return {"items": reports.changelog(conn, date_from or to - timedelta(days=30), to)}


@router.get("/api/report/weekly")
def weekly(user: Reader, conn_factory: Conn):
    with conn_factory() as conn:
        return reports.latest(conn) or {"changes": 0, "body": {"groups": []}, "sent_at": None}


@router.post("/api/report/weekly/build")
def build(user: Reader, conn_factory: Conn, clock: Now):
    """«Сформировать отчёт сейчас»."""
    with conn_factory() as conn:
        return reports.build_weekly(conn, clock.now())


def _mail(fn):
    try:
        return fn()
    except JobNotConfigured as e:
        raise HTTPException(503, {"error": "not_configured", "message": str(e)}) from None
    except mailer.MailError:
        raise HTTPException(502, {"error": "smtp", "message": "Письмо не отправлено: почтовый сервер не ответил. "
                                                              "Отчёт сохранён, повторите позже"}) from None


@router.post("/api/report/weekly/send")
def send(user: Reader, conn_factory: Conn, clock: Now):
    return _mail(lambda: mailer.send_weekly(conn_factory, clock))


@router.get("/api/report/recipients")
def recipients(user: Reader):
    """S40: адреса из MAIL_TO; меняются в .env."""
    return {"recipients": mailer.recipients()}


@router.post("/api/digest/current/send")
def send_digest(user: Annotated[auth.User, Depends(auth.require("digest.decide"))], conn_factory: Conn, clock: Now):
    """S41: недельная сводка — на почту."""
    return {"sent_to": _mail(lambda: mailer.send_digest_summary(conn_factory, clock))}
