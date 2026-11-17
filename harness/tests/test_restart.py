"""(2) Cursor and counts are exact and idempotent across kills mid-request; relay hands over with one active seat."""
import asyncio

from akhand.config import SEATS
from akhand.harness import Harness
from akhand.ledger import Ledger


def _record(monkeypatch, log):
    orig_unit, orig_skip = Ledger.commit_unit, Ledger.commit_skip

    def unit(self, **kw):
        orig_unit(self, **kw)  # recorded only after the transaction really committed
        log.append(("unit", kw["agent"], kw["cur"]))

    def skip(self, **kw):
        orig_skip(self, **kw)
        log.append(("skip", kw["agent"], kw["cur"]))

    monkeypatch.setattr(Ledger, "commit_unit", unit)
    monkeypatch.setattr(Ledger, "commit_skip", skip)


async def _life(cfg, seconds, hard):
    h = Harness(cfg)
    await h.start()
    await asyncio.sleep(seconds)
    peak = {s: h.primary.max_inflight_by_station[s] for s in (3, 6)}
    await (h.kill() if hard else h.stop())  # kill: no flush, no goodbye, in the middle of requests
    return peak


def test_exact_counts_and_single_relay_seat_across_kills(make_cfg, monkeypatch):
    log = []
    _record(monkeypatch, log)
    cfg = make_cfg(fake_error_rate=0.1, fake_wrong_rate=0.15)
    totals = []
    for seconds, hard in ((1.5, True), (1.5, True), (1.0, False)):
        peak = asyncio.run(_life(cfg, seconds, hard))
        assert peak[3] <= 1 and peak[6] <= 1, "two seats of one relay station requested at once"
        led = Ledger(cfg.data_dir / "akhand.db")
        totals.append(led.total_units())
        led.close()
    assert totals[0] > 0 and totals[0] < totals[1] < totals[2]

    led = Ledger(cfg.data_dir / "akhand.db")
    units_by_agent = led.agent_totals()
    cursors, relays = led.cursors(), led.relays()
    # every committed unit/skip moved exactly one cursor step: no double count, no gap, across all restarts
    per_key: dict = {}
    for kind, agent, cur in log:
        key = cur.key if cur.relay is None else ("relay", cur.relay[0])
        per_key.setdefault(key, []).append((kind, agent, cur))
    for key, items in per_key.items():
        poss = [c.pos for _, _, c in items]
        assert poss == list(range(poss[0], poss[0] + len(poss))), f"cursor {key} not consecutive"
        if isinstance(key, tuple):  # relay: the committing agent is the seat whose turn it is
            T = cfg.relay_turn_verses
            for _, agent, c in items:
                assert agent % SEATS == ((c.pos - 1) // T) % SEATS, "a seat recited out of turn"
                assert agent // SEATS == key[1]
    # ledger equals the recorded commits
    assert led.total_units() == sum(1 for k, _, _ in log if k == "unit")
    assert sum(u for u, _ in units_by_agent.values()) == led.total_units()
    assert sum(s for _, s in units_by_agent.values()) == sum(1 for k, _, _ in log if k == "skip")
    for station, (pos, seat, taken) in relays.items():
        T = cfg.relay_turn_verses
        assert (seat, taken) == ((pos // T) % SEATS, pos % T)  # exactly one holder, mid-turn state preserved
    assert all(p >= 0 for p in cursors.values())
    led.close()
