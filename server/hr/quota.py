"""Суточные пределы (S11, S12): общий счёт на все прогоны по (день по Москве, ключ) в ops.daily_quota.

Списание — до внешнего вызова и в своей короткой транзакции: откат транзакции резюме не отменяет учёт
просмотра, который hh уже мог списать. Неудачный вызов единицу не возвращает — счёт осторожный.
"""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from .clock import SYSTEM
from .db import ConnFactory, one
from .views import TZ

_MSK = ZoneInfo(TZ)


def day_of(now: datetime) -> date:
    """Сутки по Москве: граница — полночь рабочего места, а не UTC."""
    return now.astimezone(_MSK).date()


def take(conn_factory: ConnFactory, key: str, cap: int, *, now: datetime | None = None) -> bool:
    """Взять одну единицу предела `cap` на сегодня; False — предел исчерпан или `cap <= 0`."""
    if cap <= 0:
        return False
    day = day_of(now or SYSTEM.now())
    with conn_factory() as conn:
        r = one(conn, """INSERT INTO ops.daily_quota AS q (day, key, used) VALUES (%s, %s, 1)
                         ON CONFLICT (day, key) DO UPDATE SET used = q.used + 1 WHERE q.used < %s
                         RETURNING used""", (day, key, cap))
    return r is not None
