"""Dollar budget for the paid backup: calendar-month and rolling-7-day caps, plus pacing."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

WEEK_MIN = 7 * 24 * 60


class BudgetDry(Exception):
    pass


@dataclass
class Reservation:
    provider: str
    minute: int
    est: float


class Budget:
    """reserve (atomic check + write) before a call, settle to the real charge afterwards.
    A reservation is written to the ledger at once, so a crash mid-call leaves the estimate
    charged (conservative). Check-and-reserve has no await inside, so concurrent tasks cannot
    both pass the same remaining room."""

    def __init__(self, ledger, clock, calendar, month_cap: float, week_cap: float, *, paced: bool = True, min_est: float = 0.0003):
        self.ledger, self.clock, self.cal = ledger, clock, calendar
        self.month_cap, self.week_cap, self.paced, self.min_est = month_cap, week_cap, paced, min_est
        self.lock = asyncio.Lock()
        self._tokens: float | None = None
        self._last_refill = clock.mono()
        self._month_key = None
        self.month_usd = 0.0
        self.minutes: dict[int, float] = {}
        self._load()

    # ---- state ---------------------------------------------------------
    def _now_min(self) -> int:
        return int(self.clock.wall() // 60)

    def _load(self):
        ms, me = self.cal.month_bounds()
        self._month_key = self.cal.month_key()
        self.month_usd = self.ledger.spend_between(int(ms // 60), int(me // 60))
        self.minutes = self.ledger.spend_minutes_since(self._now_min() - WEEK_MIN)

    def _roll(self):
        if self.cal.month_key() != self._month_key:
            self._load()

    def week_usd(self) -> float:
        cutoff = self._now_min() - WEEK_MIN
        for m in [m for m in self.minutes if m < cutoff]:
            del self.minutes[m]
        return sum(self.minutes.values())

    def spent_month(self) -> float:
        self._roll()
        return self.month_usd

    def has_room(self, est: float | None = None) -> bool:
        self._roll()
        e = self.min_est if est is None else est
        return self.month_usd + e <= self.month_cap + 1e-12 and self.week_usd() + e <= self.week_cap + 1e-12

    def _apply(self, provider: str, minute: int, delta: float):
        self.ledger.spend_add(minute, provider, delta)
        self.month_usd += delta
        self.minutes[minute] = self.minutes.get(minute, 0.0) + delta

    # ---- pacing --------------------------------------------------------
    def _rate(self) -> float:
        ms, me = self.cal.month_bounds()
        secs_left = max(3600.0, me - self.clock.wall())
        month_rate = max(0.0, self.month_cap - self.month_usd) / secs_left
        return min(month_rate, self.week_cap / (7 * 86400.0))

    def _take_tokens(self, est: float) -> float:
        """Returns 0 when the dollars were taken from the bucket, else seconds to wait."""
        rate = self._rate()
        cap = max(rate * 900.0, est * 2)
        now = self.clock.mono()
        if self._tokens is None:
            self._tokens = cap
        self._tokens = min(cap, self._tokens + rate * max(0.0, now - self._last_refill))
        self._last_refill = now
        if self._tokens >= est:
            self._tokens -= est
            return 0.0
        return min(30.0, (est - self._tokens) / rate) if rate > 0 else 30.0

    # ---- API -----------------------------------------------------------
    async def acquire(self, provider: str, est: float) -> Reservation:
        while True:
            async with self.lock:
                self._roll()
                if not self.has_room(est):
                    raise BudgetDry(f"no room for {est:.5f} (month {self.month_usd:.4f}/{self.month_cap}, week {self.week_usd():.4f}/{self.week_cap})")
                wait = self._take_tokens(est) if self.paced else 0.0
                if wait == 0.0:
                    minute = self._now_min()
                    self._apply(provider, minute, est)
                    return Reservation(provider, minute, est)
            await asyncio.sleep(max(0.2, wait))

    def settle(self, res: Reservation, actual: float):
        """Replace the estimate with the real charge (it may exceed the estimate; it is recorded as is)."""
        self._apply(res.provider, res.minute, actual - res.est)

    def status(self) -> dict:
        return {"month_usd": round(self.spent_month(), 6), "week_usd": round(self.week_usd(), 6),
                "month_cap": self.month_cap, "week_cap": self.week_cap}
