"""Часы, которые можно подменить в тестах: ограничитель запросов и сроки берут время отсюда."""
from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta


class Clock:
    def now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)


class FakeClock(Clock):
    """Время идёт только когда его двигают: sleep() не ждёт, а прибавляет."""

    def __init__(self, start: datetime | None = None):
        self._now = start or datetime(2026, 10, 26, 6, 30, tzinfo=UTC)
        self._mono = 1000.0
        self.slept: list[float] = []

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return self._mono

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            self.slept.append(seconds)
            self.advance(seconds)

    def advance(self, seconds: float = 0, days: float = 0) -> None:
        self._mono += seconds + days * 86400
        self._now += timedelta(seconds=seconds, days=days)


SYSTEM = Clock()
