"""Per-second batching of verified units, and the pacing of each agent."""
from __future__ import annotations

import asyncio
import random
from collections import deque

MAX_AGE_S = 10.0


class Events:
    """add() is called right after a unit is committed to the ledger. tick() (once a second)
    turns the pending counts into a batch; batches older than 10 s are dropped unsent."""

    def __init__(self, clock):
        self.clock = clock
        self.cur: dict[int, int] = {}
        self.q: deque[tuple[float, float, list]] = deque()
        self.dropped_units = 0

    def add(self, agent: int, n: int = 1):
        self.cur[agent] = self.cur.get(agent, 0) + n

    def tick(self):
        if self.cur:
            ev = sorted([a, n] for a, n in self.cur.items())
            self.q.append((self.clock.mono(), self.clock.wall(), ev))
            self.cur = {}
        cut = self.clock.mono() - MAX_AGE_S
        while self.q and self.q[0][0] < cut:
            _, _, ev = self.q.popleft()
            self.dropped_units += sum(n for _, n in ev)

    def peek(self):
        return self.q[0] if self.q else None

    def pop(self):
        return self.q.popleft()


class Pacer:
    """Each agent recites at most TARGET_VERSES_PER_AGENT_HOUR; start times are spread out."""

    def __init__(self, verses_per_hour: float, clock, spread_s: float = 20.0):
        self.interval = 3600.0 / max(1e-6, verses_per_hour)
        self.clock = clock
        self.spread = spread_s
        self.next: dict[int, float] = {}

    async def wait(self, agent: int):
        now = self.clock.mono()
        t = self.next.get(agent)
        if t is None:
            t = now + random.uniform(0, min(self.spread, self.interval * 4))
            self.next[agent] = t
        if t > now:
            await asyncio.sleep(t - now)

    def done(self, agent: int, started: float, units: int):
        self.next[agent] = max(self.clock.mono(), started + max(1, units) * self.interval * random.uniform(0.9, 1.1))

    def hold(self, agent: int, delay: float):
        self.next[agent] = self.clock.mono() + delay
