"""(3) The backup budget never exceeds the month or the 7-day cap under concurrency."""
import asyncio
import random
from datetime import timezone

from akhand.budget import Budget, BudgetDry
from akhand.clock import Calendar, ManualClock
from akhand.ledger import Ledger


def _budget(tmp_path, clock, month, week):
    led = Ledger(tmp_path / "b.db")
    return led, Budget(led, clock, Calendar(timezone.utc, clock, led), month, week, paced=False)


def test_caps_hold_under_concurrency_and_survive_restart(tmp_path):
    clock = ManualClock()
    led, b = _budget(tmp_path, clock, month=0.020, week=0.012)
    est = 0.001
    seen_max = {"m": 0.0, "w": 0.0}
    results = {"ok": 0, "dry": 0}

    async def worker(i):
        rnd = random.Random(i)
        for _ in range(5):
            try:
                res = await b.acquire("muse", est)
            except BudgetDry:
                results["dry"] += 1
                continue
            results["ok"] += 1
            seen_max["m"] = max(seen_max["m"], b.month_usd)
            seen_max["w"] = max(seen_max["w"], b.week_usd())
            await asyncio.sleep(rnd.random() * 0.01)  # the call is in flight; others keep reserving
            b.settle(res, est * rnd.uniform(0.2, 1.0))  # real charge never above the reservation here

    async def main():
        await asyncio.gather(*[worker(i) for i in range(200)])

    asyncio.run(main())
    assert results["ok"] > 5 and results["dry"] > 0
    assert seen_max["m"] <= 0.020 + 1e-9 and seen_max["w"] <= 0.012 + 1e-9
    assert b.month_usd <= 0.020 + 1e-9 and b.week_usd() <= 0.012 + 1e-9

    # a restart reads the same totals from the ledger, and the week window rolls
    b2 = Budget(led, clock, Calendar(timezone.utc, clock, led), 0.020, 0.012, paced=False)
    assert abs(b2.month_usd - b.month_usd) < 1e-9 and abs(b2.week_usd() - b.week_usd()) < 1e-9
    clock.advance(8 * 86400)
    assert b2.week_usd() == 0.0 and b2.has_room(0.001)
    led.close()
