"""Clocks. Wall time for calendars and budgets, monotonic time for intervals."""
from __future__ import annotations

import time
from datetime import date, datetime


class Clock:
    def mono(self) -> float:
        return time.monotonic()

    def wall(self) -> float:
        return time.time()


class ManualClock(Clock):
    """Test clock: advance() moves both, jump_wall() moves only the wall clock."""

    def __init__(self, mono=1000.0, wall=1_794_000_000.0):
        self._m, self._w = mono, wall

    def mono(self):
        return self._m

    def wall(self):
        return self._w

    def advance(self, s):
        self._m += s
        self._w += s

    def jump_wall(self, s):
        self._w += s


class Calendar:
    """Local calendar day that never moves backwards on a small clock step."""

    TOLERANCE_DAYS = 2

    def __init__(self, tz, clock: Clock, ledger=None):
        self.tz, self.clock, self.ledger = tz, clock, ledger
        self._hw: date | None = None
        if ledger is not None:
            v = ledger.meta_get("hw_day")
            if v:
                self._hw = date.fromisoformat(v)

    def now_local(self) -> datetime:
        return datetime.fromtimestamp(self.clock.wall(), self.tz)

    def today(self) -> date:
        d = self.now_local().date()
        hw = self._hw
        if hw is None or d > hw or (hw - d).days > self.TOLERANCE_DAYS:
            self._hw = d
            if self.ledger is not None:
                try:
                    self.ledger.meta_set("hw_day", d.isoformat())
                except Exception:
                    pass
        return self._hw

    def today_iso(self) -> str:
        return self.today().isoformat()

    def month_key(self) -> tuple[int, int]:
        d = self.today()
        return d.year, d.month

    def month_bounds(self) -> tuple[float, float]:
        d = self.today()
        start = datetime(d.year, d.month, 1, tzinfo=self.tz)
        ny, nm = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
        return start.timestamp(), datetime(ny, nm, 1, tzinfo=self.tz).timestamp()
