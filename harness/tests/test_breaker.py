"""(4) The breaker fails over and returns; auth errors lead to resting, not a retry storm;
a long Retry-After cools a free provider down (and survives a restart)."""
import asyncio
from datetime import timezone


from akhand.breaker import Breaker
from akhand.budget import Budget, BudgetDry
from akhand.clock import Calendar, ManualClock
from akhand.harness import Harness
from akhand.ledger import Ledger
from akhand.provider import FakeProvider, classify_http
from akhand.router import Router


def test_failover_probe_and_return(tmp_path):
    clock = ManualClock()
    led = Ledger(tmp_path / "r.db")
    budget = Budget(led, clock, Calendar(timezone.utc, clock, led), 30, 7, paced=False)
    prim, back = FakeProvider("iceman"), FakeProvider("muse", price_in=0.1, price_out=0.2, reserve_usd=0.0003)
    r = Router([prim], back, budget, clock, breaker_factory=lambda: Breaker(clock, 300, 10, 900))

    def call(ok):
        route = r.choose()
        r.report(route, ok=ok, kind=None if ok else "server")
        return route

    for _ in range(10):
        assert call(False).role == "free"
    assert r.choose().role == "backup" and r.label() == "muse"  # tripped: all agents on the backup

    clock.advance(899)
    assert r.choose().role == "backup"
    clock.advance(2)
    probe, other = r.choose(), r.choose()
    assert probe.role == "free" and probe.probe and other.role == "backup"  # exactly one probe at a time
    r.report(probe, ok=False, kind="server")  # probe fails: stay on backup, next probe in 15 min
    assert r.choose().role == "backup"
    clock.advance(901)
    probe = r.choose()
    assert probe.probe
    r.report(probe, ok=True)
    assert r.choose().role == "free" and not r.choose().probe and r.label() == "iceman"
    led.close()


def test_auth_errors_on_both_providers_lead_to_resting_without_a_storm(make_cfg):
    cfg = make_cfg()
    prim = FakeProvider("iceman", latency=0.05)
    back = FakeProvider("muse", latency=0.05, price_in=0.1, price_out=0.2, reserve_usd=0.0003)
    prim.always = back.always = "auth"

    async def go():
        h = Harness(cfg, free=[prim], backup=back)
        await h.start()
        await asyncio.sleep(1.2)
        mid = (prim.calls, back.calls)
        await asyncio.sleep(1.5)
        end = (prim.calls, back.calls)
        mode, units, spent = h.mode(), h.ledger.total_units(), h.budget.month_usd
        await h.stop()
        return mid, end, mode, units, spent

    mid, end, mode, units, spent = asyncio.run(go())
    assert end == mid, "calls continued after both providers answered 401"
    assert max(mid) <= cfg.max_concurrency and sum(mid) > 0
    assert mode == "resting" and units == 0 and abs(spent) < 1e-9


def _cooldown_rig(tmp_path, month=30.0, week=7.0):
    clock = ManualClock()
    led = Ledger(tmp_path / "c.db")
    budget = Budget(led, clock, Calendar(timezone.utc, clock, led), month, week, paced=False)
    free = [FakeProvider("iceman"), FakeProvider("beam"), FakeProvider("longcat")]
    back = FakeProvider("muse", price_in=0.1, price_out=0.2, reserve_usd=0.0003)

    def router():
        return Router(free, back, budget, clock, ledger=led, breaker_factory=lambda: Breaker(clock, 60, 10, 900))
    return clock, led, budget, router


def test_long_retry_after_moves_to_next_free_provider_and_recovers(tmp_path):
    assert classify_http(429, "{}", "41042").retry_after == 41042
    assert classify_http(429, "{}", "Wed, 21 Oct 2099 07:28:00 GMT").retry_after == 26 * 3600  # capped
    clock, led, _, make = _cooldown_rig(tmp_path)
    r = make()
    first = r.choose()
    assert first.provider.name == "iceman" and not first.probe
    assert r.report(first, ok=False, kind="rate_limit", retry_after=41042) is True  # cooled: caller re-routes at once
    seen = set()
    for _ in range(50):
        route = r.choose()
        seen.add(route.provider.name)
        r.report(route, ok=True)
    assert seen == {"beam"}  # never iceman again, no silent gap, no backup
    assert r.provider_health()["iceman"]["state"] == "cooldown" and r.provider_health()["iceman"]["cooldown_until"]
    clock.advance(41041)
    assert r.choose().provider.name == "beam"
    clock.advance(2)
    probe = r.choose()
    assert probe.provider.name == "iceman" and probe.probe
    assert r.choose().provider.name == "beam"  # one probe at a time
    r.report(probe, ok=True)
    assert r.choose().provider.name == "iceman" and r.provider_health()["iceman"]["state"] == "ok"
    led.close()


def test_all_free_cooling_uses_backup_within_budget_then_rests(tmp_path):
    clock, led, budget, make = _cooldown_rig(tmp_path, month=0.002, week=0.002)
    r = make()
    for name in ("iceman", "beam", "longcat"):
        route = r.choose()
        assert route.provider.name == name
        r.report(route, ok=False, kind="rate_limit", retry_after=30000)
    assert r.choose().role == "backup" and r.label() == "muse"

    async def spend():
        got = 0
        for _ in range(5):
            try:
                res = await budget.acquire("muse", 0.001)
            except BudgetDry:
                break
            budget.settle(res, 0.001)
            got += 1
        return got
    assert asyncio.run(spend()) == 2  # the caps still hold: two calls at 0.001 under 0.002
    assert r.choose() is None and not r.usable()  # nothing left: rest
    led.close()


def test_cooldown_survives_restart(tmp_path):
    clock, led, _, make = _cooldown_rig(tmp_path)
    r = make()
    route = r.choose()
    r.report(route, ok=False, kind="rate_limit", retry_after=3600)
    led.close()
    led2 = Ledger(tmp_path / "c.db")
    clock2 = clock  # same wall time after the restart
    budget = Budget(led2, clock2, Calendar(timezone.utc, clock2, led2), 30, 7, paced=False)
    r2 = Router([FakeProvider("iceman"), FakeProvider("beam"), FakeProvider("longcat")], FakeProvider("muse", price_in=0.1, price_out=0.2),
                budget, clock2, ledger=led2)
    for _ in range(20):
        assert r2.choose().provider.name == "beam"  # iceman is not called while it is still cooling down
    clock2.advance(3601)
    assert r2.choose().provider.name == "iceman" and r2.choose().provider.name == "beam"
    led2.close()
