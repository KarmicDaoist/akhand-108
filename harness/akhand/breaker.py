"""Circuit breaker, one per free provider."""
from __future__ import annotations

from collections import deque


class Breaker:
    """Trips when more than half of at least min_calls calls in the window failed; probe_due() says when
    to try the provider again; probe_result() closes or re-arms it; reset() clears it (a cooldown supersedes it)."""

    def __init__(self, clock, window_s=300.0, min_calls=10, probe_interval_s=900.0):
        self.clock, self.window_s, self.min_calls, self.probe_interval_s = clock, window_s, min_calls, probe_interval_s
        self.events: deque[tuple[float, bool]] = deque()
        self.tripped = False
        self.next_probe = 0.0

    def _prune(self):
        cut = self.clock.mono() - self.window_s
        while self.events and self.events[0][0] < cut:
            self.events.popleft()

    def record(self, ok: bool) -> bool:
        """Record a normal call. Returns True if this call tripped the breaker."""
        if self.tripped:
            return False
        self.events.append((self.clock.mono(), ok))
        self._prune()
        n = len(self.events)
        fails = sum(1 for _, o in self.events if not o)
        if n >= self.min_calls and fails * 2 > n:
            self.tripped = True
            self.next_probe = self.clock.mono() + self.probe_interval_s
            return True
        return False

    def reset(self):
        self.tripped = False
        self.events.clear()

    def probe_due(self) -> bool:
        return self.tripped and self.clock.mono() >= self.next_probe

    def probe_result(self, ok: bool):
        if ok:
            self.tripped = False
            self.events.clear()
        else:
            self.next_probe = self.clock.mono() + self.probe_interval_s

    def failure_rate(self) -> tuple[int, int]:
        self._prune()
        return sum(1 for _, o in self.events if not o), len(self.events)
