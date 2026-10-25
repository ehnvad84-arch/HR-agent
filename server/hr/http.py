"""HTTP к площадкам: таймауты, не чаще запроса в секунду на площадку, бюджет страниц (раздел 7.1 ТЗ)."""
from __future__ import annotations

import threading

import httpx

from .clock import SYSTEM, Clock
from .jobs import JobStats

TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def client(user_agent: str | None = None, **kw) -> httpx.Client:
    headers = {"User-Agent": user_agent} if user_agent else {}
    return httpx.Client(timeout=TIMEOUT, headers=headers, follow_redirects=False, **kw)


class RateLimiter:
    """Интервал между запросами к одной площадке не меньше 1/per_sec секунды."""

    def __init__(self, clock: Clock | None = None, per_sec: float = 1.0):
        self.clock = clock or SYSTEM
        self.interval = 1.0 / per_sec
        self.last: dict[str, float] = {}
        self.lock = threading.Lock()

    def wait(self, source: str) -> None:
        with self.lock:
            prev = self.last.get(source)
            if prev is not None:
                self.clock.sleep(self.interval - (self.clock.monotonic() - prev))
            self.last[source] = self.clock.monotonic()


class PageBudget:
    """Не больше limit страниц за прогон; каждый запрос к площадке — страница."""

    def __init__(self, limit: int, stats: JobStats):
        self.limit = limit
        self.stats = stats

    def take(self) -> bool:
        if self.stats.pages >= self.limit:
            self.stats.limit_reached = True
            self.stats.note = f"достигнут предел {self.limit} страниц за прогон"
            return False
        self.stats.pages += 1
        return True

    @property
    def left(self) -> int:
        return max(0, self.limit - self.stats.pages)
