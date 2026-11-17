"""End-to-end soak: real main loop, fake provider, dummy relay sink, hard kill and restart.

    python harness/tools/soak.py [--seconds 60]
"""
import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from akhand.contract import validate  # noqa: E402
from akhand.ledger import Ledger  # noqa: E402
from akhand.library import load_library  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "library.json"


class Sink:
    def __init__(self):
        self.runs: list[list[dict]] = []
        self.bad_auth = 0
        self.port = 0

    async def handler(self, ws):
        if ws.request.headers.get("Authorization") != "Bearer soak-token":
            self.bad_auth += 1
            await ws.close()
            return
        try:
            async for raw in ws:
                self.runs[-1].append(json.loads(raw))
        except Exception:
            pass  # the harness was killed on purpose

    async def start(self):
        from websockets.asyncio.server import serve
        self.server = await serve(self.handler, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]


def env_for(tmp: Path, port: int, gather_at: str) -> dict:
    e = dict(os.environ)
    e.update({
        "DATA_DIR": str(tmp / "data"), "LIBRARY_PATH": str(FIXTURE), "PROVIDER": "fake",
        "FAKE_ERROR_RATE": "0.1", "FAKE_WRONG_RATE": "0.1", "FAKE_LATENCY": "0.3",
        "TARGET_VERSES_PER_AGENT_HOUR": "36000", "TZ": "Asia/Kolkata", "GATHERING_TIME": gather_at,
        "RELAY_URL": f"ws://127.0.0.1:{port}/ingest", "RELAY_TOKEN": "soak-token",
        "STATUS_EXPORT_PATH": str(tmp / "export" / "status.json"), "STATUS_EXPORT_EVERY_S": "5",
        "CONTROL_PATH": str(tmp / "export" / "control.json"), "CONTROL_POLL_S": "2",
        "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8",
    })
    return e


def ctl(env, *args):
    r = subprocess.run([sys.executable, "-m", "akhand.ctl", *args], cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8")
    return r.returncode, r.stdout.strip()


def spawn(env, logf):
    return subprocess.Popen([sys.executable, "-m", "akhand.main"], cwd=ROOT, env=env, stdout=logf, stderr=subprocess.STDOUT)


def ledger_totals(data: Path):
    led = Ledger(data / "akhand.db", create=False)
    out = dict(units=led.total_units(), agent=led.agent_totals(), cursors=led.cursors(), relays=led.relays(),
               stuck=led.stuck_count(), gather=led._q("SELECT day, complete FROM gathering"))
    led.close()
    return out


def batch_sum(msgs):
    return sum(n for m in msgs if m.get("t") == "batch" for _, n in m["ev"])


def check_messages(msgs):
    errs = []
    for m in msgs:
        errs += [f"{m.get('t')}: {e}" for e in validate(m)]
    kinds = {}
    for m in msgs:
        kinds[m.get("t")] = kinds.get(m.get("t"), 0) + 1
    return kinds, errs


def invariants(data: Path, tot) -> list[str]:
    """units + skips of each agent == how far its cursors moved; relay station pos == sum over its seats."""
    lib = load_library(FIXTURE)
    bad = []
    gather = {}
    for k, p in tot["cursors"].items():
        if k.startswith("g:"):
            gather[int(k.rsplit(":", 1)[1])] = gather.get(int(k.rsplit(":", 1)[1]), 0) + p
    for station in lib.stations:
        for seat in range(9):
            a = station.index * 9 + seat
            units, skips = tot["agent"].get(a, (0, 0))
            g = gather.get(a, 0)
            if station.mode == "relay":
                continue
            n = len(station.verses_for(seat))
            default = seat * n // 9 if station.mode == "parallel" else 0
            moved = tot["cursors"].get(f"a:{a}", default) - default
            if units + skips != moved + g:
                bad.append(f"agent {a}: units+skips {units + skips} != cursor moved {moved} + gathering {g}")
        if station.mode == "relay":
            seats = [tot["agent"].get(station.index * 9 + s, (0, 0)) for s in range(9)]
            relay_total = sum(u + s for u, s in seats) - sum(gather.get(station.index * 9 + s, 0) for s in range(9))
            pos = tot["relays"].get(station.index, (0, 0, 0))[0]
            if relay_total != pos:
                bad.append(f"relay station {station.index}: seats {relay_total} != shared cursor {pos}")
    return bad


async def quota_run(seconds: int):
    """Run 4: first free model runs out of allowance (429 + long Retry-After), the second takes over, then it runs out
    too, then the third, then the paid backup carries on. Totals must stay exact."""
    tmp = Path(tempfile.mkdtemp(prefix="akhand-soak-quota-"))
    (tmp / "export").mkdir()
    sink = Sink()
    await sink.start()
    sink.runs.append([])
    env = env_for(tmp, sink.port, "23:59")
    env.update({"FAKE_QUOTA": "iceman:60,beam:120,longcat:180", "FAKE_QUOTA_RETRY_AFTER_S": "3600",
                "FAKE_ERROR_RATE": "0.03", "FAKE_WRONG_RATE": "0.03", "FAKE_LATENCY": "0.2"})
    log = open(tmp / "soak.log", "w", encoding="utf-8")
    loop = asyncio.get_running_loop()
    p = spawn(env, log)
    print(f"run 4: quota exhaustion, {seconds}s: iceman 60 calls, beam 120, longcat 180, Retry-After 3600 s, backup = paid fake")
    t0 = time.time()
    timeline, last = [], None
    while time.time() - t0 < seconds:
        await asyncio.sleep(1.0)
        code, js = await loop.run_in_executor(None, lambda: ctl(env, "status", "--json"))
        try:
            st = json.loads(js)
        except ValueError:
            continue
        now = (st.get("model"), st.get("provider"))
        if now != last:
            timeline.append((round(time.time() - t0, 1), st.get("model"), st.get("provider"), st.get("health")))
            last = now
    final = json.loads((await loop.run_in_executor(None, lambda: ctl(env, "status", "--json")))[1])
    await loop.run_in_executor(None, lambda: ctl(env, "stop"))
    await loop.run_in_executor(None, lambda: p.wait(timeout=40))
    log.close()
    tot = ledger_totals(tmp / "data")
    msgs = list(sink.runs[-1])
    kinds, errs = check_messages(msgs)
    ts = [m["ts"] for m in msgs if m.get("t") == "batch"]
    gaps = [(b - a) / 1000 for a, b in zip(ts, ts[1:]) if a > ts[0] + 3000]
    led = Ledger(tmp / "data" / "akhand.db", create=False)
    cooldowns = {k: v for k, v in led._q("SELECT key, value FROM meta WHERE key LIKE 'cooldown:%'")}
    led.close()
    print("  provider in use over time (t seconds, model, provider, health):")
    for row in timeline:
        print("    ", row)
    print("  providers at end:", json.dumps(final["providers"]))
    print(f"  persisted cooldowns (epoch): {cooldowns}")
    print(f"  longest gap between per-second batches after startup: {max(gaps, default=0):.1f}s (under 5 s: {max(gaps, default=0) < 5})")
    print(f"  sum of batch events: {batch_sum(msgs)}  ledger total: {tot['units']}  equal: {batch_sum(msgs) == tot['units']}  contract violations: {len(errs)}")
    print(f"  spend: {final['spend']}; stuck verses: {tot['stuck']}")
    print("  invariants:", invariants(tmp / "data", tot) or "all agent/relay cursors consistent")
    tail = (tmp / "soak.log").read_text(encoding="utf-8").splitlines()
    bad = [l for l in tail if " ERROR " in l or "Traceback" in l or " CRITICAL " in l]
    print(f"  harness log error/traceback lines: {len(bad)} {bad[:3]}")
    for l in tail:
        if "cooling it down" in l or "back after" in l:
            print("   log:", l[:200])


async def main(seconds: int):
    tmp = Path(tempfile.mkdtemp(prefix="akhand-soak-"))
    (tmp / "export").mkdir()
    sink = Sink()
    await sink.start()
    gather_at = (datetime.now(timezone(timedelta(hours=5, minutes=30))) + timedelta(seconds=20)).strftime("%H:%M:%S")
    gather_at = gather_at[:5]  # HH:MM; gathering fires at the next minute boundary after startup
    env = env_for(tmp, sink.port, gather_at)
    log = open(tmp / "soak.log", "w", encoding="utf-8")
    loop = asyncio.get_running_loop()
    results = {}

    def sh(*a):
        return loop.run_in_executor(None, lambda: ctl(env, *a))

    # ---------------- run 1: graceful, `seconds` long ----------------
    sink.runs.append([])
    t0 = time.time()
    p = spawn(env, log)
    print(f"run 1: {seconds}s, fake error 10%, wrong word 10%, gathering at {gather_at} IST, tmp={tmp}")
    await asyncio.sleep(seconds * 0.5)
    code, js = await sh("status", "--json")
    mid_status = json.loads(js)
    print(f"  ctl status exit code at t={time.time() - t0:.0f}s: {code}")
    (tmp / "export" / "control.json").write_text(json.dumps({"command": "pause", "id": "t1"}))
    await asyncio.sleep(5)
    paused = json.loads((await sh("status", "--json"))[1])
    (tmp / "export" / "control.json").write_text(json.dumps({"command": "resume", "id": "t2"}))
    await asyncio.sleep(4)
    resumed = json.loads((await sh("status", "--json"))[1])
    (tmp / "export" / "control.json").write_text("{not json")  # malformed: must be ignored
    await asyncio.sleep(max(1, seconds - (time.time() - t0)))
    code, js = await sh("status", "--json")
    final_status = json.loads(js)
    await sh("stop")
    await loop.run_in_executor(None, lambda: p.wait(timeout=40))
    rc1 = p.returncode
    tot1 = ledger_totals(tmp / "data")
    msgs1 = list(sink.runs[-1])
    kinds, errs = check_messages(msgs1)
    exported = json.loads((tmp / "export" / "status.json").read_text())
    print(f"  stopped with SIGTERM-style stop, exit code {rc1}")
    print(f"  units verified (ledger): {tot1['units']}   corrections today: {final_status['today']['corrections']}   stuck verses: {tot1['stuck']}")
    print(f"  messages at sink: {kinds}; contract violations: {len(errs)} {errs[:3]}")
    print(f"  sum of batch events: {batch_sum(msgs1)}  ledger total: {tot1['units']}  equal: {batch_sum(msgs1) == tot1['units']}")
    print(f"  gathering rows (day, complete): {tot1['gather']}")
    print(f"  control file: paused during pause window={paused['paused']} mode={paused['mode']}; after resume paused={resumed['paused']}")
    print(f"  status export has exported_at={exported['exported_at']} (age {time.time() - exported['exported_at']:.0f}s), health={exported['health']}")
    print("  invariants:", invariants(tmp / "data", tot1) or "all agent/relay cursors consistent")
    print("  ctl status --json (running, near the end):")
    print("    " + json.dumps({k: final_status[k] for k in ("mode", "provider", "health", "exit_code", "paused", "today", "all_time", "spend",
                                                          "seconds_since_last_unit", "stuck_verses", "relay_connected", "providers")}))
    results["run1"] = tot1["units"]

    # ---------------- run 2: hard kill mid-flight ----------------
    sink.runs.append([])
    p = spawn(env, log)
    await asyncio.sleep(8)
    p.kill()
    p.wait()
    tot2 = ledger_totals(tmp / "data")
    sent2 = batch_sum(sink.runs[-1])
    print(f"run 2: hard kill after 8s: ledger {tot1['units']} -> {tot2['units']} (+{tot2['units'] - tot1['units']}), batches seen at sink during run 2: {sent2}")

    # ---------------- run 3: restart 10s ----------------
    sink.runs.append([])
    p = spawn(env, log)
    await asyncio.sleep(10)
    await sh("stop")
    await loop.run_in_executor(None, lambda: p.wait(timeout=40))
    tot3 = ledger_totals(tmp / "data")
    sent3 = batch_sum(sink.runs[-1])
    kinds3, errs3 = check_messages(sink.runs[-1])
    print(f"run 3: restart 10s, graceful stop (exit {p.returncode}): ledger {tot2['units']} -> {tot3['units']} (+{tot3['units'] - tot2['units']}), "
          f"batches in run 3: {sent3}, equal: {tot3['units'] - tot2['units'] == sent3}, contract violations: {len(errs3)}")
    print(f"  totals only went up: {tot1['units'] <= tot2['units'] <= tot3['units']};  per-agent totals never decreased: "
          f"{all(tot3['agent'].get(a, (0, 0))[0] >= v[0] for a, v in tot1['agent'].items())}")
    print("  invariants after kill+restart:", invariants(tmp / "data", tot3) or "all agent/relay cursors consistent")
    print(f"  bad-auth connections at sink: {sink.bad_auth}")
    log.close()
    tail = (tmp / "soak.log").read_text(encoding="utf-8").splitlines()
    errs_in_log = [l for l in tail if " ERROR " in l or "Traceback" in l or " CRITICAL " in l]
    print(f"  harness log: {len(tail)} lines, error/traceback lines: {len(errs_in_log)} {errs_in_log[:3]}")
    await sink.server.wait_closed() if False else None
    await quota_run(max(30, seconds // 2))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=60)
    asyncio.run(main(ap.parse_args().seconds))
