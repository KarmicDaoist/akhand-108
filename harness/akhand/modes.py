"""Station modes: what each agent recites next, and how its cursor moves."""
from __future__ import annotations

import asyncio

from .config import SEATS
from .ledger import CursorUpdate
from .library import Library, Verse
from .verify import normalize

WAIT = object()  # relay: it is not this seat's turn


class Plan:
    """One request's worth of verses plus the cursor arithmetic for committing them in order."""

    def __init__(self, kind, agent, station, verses, pos0, *, key=None, relay0=None, turn=0):
        self.kind, self.agent, self.station, self.verses, self.pos0 = kind, agent, station, verses, pos0
        self.key, self.relay0, self.turn = key, relay0, turn  # relay0 = (seat, taken)

    def after(self, i: int) -> CursorUpdate:
        """Cursor state once verse index i (0-based, in order) is verified or skipped."""
        pos = self.pos0 + i + 1
        if self.kind == "relay":
            seat, taken = self.relay0
            taken += i + 1
            if taken >= self.turn:
                seat, taken = (seat + 1) % SEATS, 0
            return CursorUpdate(None, pos, (self.station, pos, seat, taken))
        return CursorUpdate(self.key, pos)

    def fail_key(self, i: int):
        return (self.kind, self.station if self.kind == "relay" else self.agent, self.key, self.pos0 + i)


class Directory:
    def __init__(self, lib: Library, cfg, ledger, sankalpa_line: str):
        self.lib, self.cfg = lib, cfg
        self.cursors: dict[str, int] = ledger.cursors()
        self.relay: dict[int, list[int]] = {s: [0, 0, 0] for s, st in enumerate(lib.stations)}
        for s, (pos, seat, taken) in ledger.relays().items():
            self.relay[s] = [pos, seat, taken]
        self._ev = {s: asyncio.Event() for s in range(len(lib.stations))}
        self.set_sankalpa([sankalpa_line])

    def set_sankalpa(self, lines: list[str]):
        """The gathering opens with these lines, one verse each (one line in simple mode, the full sankalpa otherwise)."""
        opening = [Verse(-1, "sankalpa", "Sankalpa", k, ln, normalize(ln)) for k, ln in enumerate(lines, 1)]
        lib = self.lib
        self.gather: list[Verse] = opening + lib.gathering["ganesh"] + lib.gathering["shiv"] + lib.gathering["durga"]

    # ---- planning ------------------------------------------------------
    def plan(self, agent: int, phase: str, day: str):
        station, seat = divmod(agent, SEATS)
        st = self.lib.stations[station]
        k = max(1, self.cfg.verses_per_call)
        if phase == "gathering":
            key = f"g:{day}:{agent}"
            pos0 = self.cursors.get(key, 0)
            if pos0 >= len(self.gather):
                return None
            return Plan("gather", agent, station, self.gather[pos0:pos0 + k], pos0, key=key)
        if st.mode == "relay":
            pos, hseat, taken = self.relay[station]
            if hseat != seat:
                return WAIT
            n = len(st.verses)
            cnt = max(1, min(k, self.cfg.relay_turn_verses - taken))
            return Plan("relay", agent, station, [st.verses[(pos + i) % n] for i in range(cnt)], pos,
                        relay0=(hseat, taken), turn=self.cfg.relay_turn_verses)
        vs = st.verses_for(seat)
        n = len(vs)
        key = f"a:{agent}"
        default = (seat * n // SEATS) if st.mode == "parallel" else 0  # staggered offsets
        pos0 = self.cursors.get(key, default)
        cnt = self.cfg.japa_reps if st.mode in ("japa", "japa-per-seat") else k
        return Plan(st.mode, agent, station, [vs[(pos0 + i) % n] for i in range(cnt)], pos0, key=key)

    def gather_done(self, day: str, agent: int) -> bool:
        return self.cursors.get(f"g:{day}:{agent}", 0) >= len(self.gather)

    # ---- state ---------------------------------------------------------
    def apply(self, cur: CursorUpdate):
        """Mirror a committed cursor in memory and wake relay waiters on a handover."""
        if cur.relay is not None:
            s, pos, seat, taken = cur.relay
            changed = self.relay[s][1] != seat
            self.relay[s] = [pos, seat, taken]
            if changed:
                ev = self._ev[s]
                self._ev[s] = asyncio.Event()
                ev.set()
        else:
            self.cursors[cur.key] = cur.pos

    async def wait_turn(self, station: int, timeout: float = 5.0):
        try:
            await asyncio.wait_for(self._ev[station].wait(), timeout)
        except asyncio.TimeoutError:
            pass

    def relay_holders(self) -> dict[str, int]:
        return {str(s): self.relay[s][1] for s, st in enumerate(self.lib.stations) if st.mode == "relay"}
