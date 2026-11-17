#!/usr/bin/env python3
"""Head-priest watch for Akhand 108. Runs inside the Hermes container as a no-agent cron.

Reads /opt/data/akhand/status.json (written every 30 s by the akhand container).
  watch  : prints nothing when all is well, so nothing is delivered. Prints one short
           message when the state changes to a problem, and one when it recovers.
  daily  : always prints the daily report.
"""
import json
import sys
import time
from pathlib import Path

BASE = Path("/opt/data/akhand")
STATUS = BASE / "status.json"
MEMO = BASE / "watch-state.json"
STALE_S = 300


def load():
    try:
        st = json.loads(STATUS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if time.time() - st.get("exported_at", 0) > STALE_S:
        return None
    return st


def problem(st):
    if st is None:
        return "down", "The prayer program is not reporting (no fresh status for 5 minutes). Docker should restart it; if this repeats, check the akhand container."
    if st.get("paused"):
        return "paused", "The prayers are paused (someone asked for a pause)."
    spend = st.get("spend", {})
    if st.get("mode") == "resting":
        return "resting", (
            "The 108 agents are resting: no model is usable right now. "
            f"Muse spend this month ${spend.get('month_usd', 0):.2f} of ${spend.get('month_cap', 0):.0f}, "
            f"last 7 days ${spend.get('week_usd', 0):.2f} of ${spend.get('week_cap', 0):.0f}."
        )
    if st.get("provider") == "muse":
        return "backup", (
            "The free model is unavailable, so the agents are praying slowly on Muse (paid backup). "
            f"Spend this month ${spend.get('month_usd', 0):.2f} of ${spend.get('month_cap', 0):.0f}."
        )
    if not st.get("relay_connected") and st.get("relay_configured"):
        return "relay", "The agents are praying, but the live page is not receiving events (relay disconnected)."
    return "ok", ""


def watch():
    st = load()
    kind, text = problem(st)
    try:
        last = json.loads(MEMO.read_text(encoding="utf-8")).get("kind", "ok")
    except (OSError, ValueError):
        last = "ok"
    if kind != last:
        try:
            MEMO.write_text(json.dumps({"kind": kind, "at": int(time.time())}), encoding="utf-8")
        except OSError:
            pass
        if kind == "ok":
            print("Akhand 108: back to normal. All 108 agents are praying on the free model.")
        else:
            print("Akhand 108: " + text)


def daily():
    st = load()
    if st is None:
        print("Akhand 108 daily report: the prayer program is not reporting. Please check the akhand container.")
        return
    t, a, sp = st.get("today", {}), st.get("all_time", {}), st.get("spend", {})
    stuck = st.get("stuck_verses", {}).get("count", 0)
    lines = [
        "Akhand 108 daily report",
        f"State: {st.get('mode')} on {st.get('provider')}" + (" (paused)" if st.get("paused") else ""),
        f"Today so far: {t.get('units', 0):,} recitations, {t.get('malas', 0):,} malas, {t.get('corrections', 0):,} re-chanted after a wrong word",
        f"All time: {a.get('units', 0):,} recitations, {a.get('malas', 0):,} malas",
        f"Muse spend: ${sp.get('month_usd', 0):.2f} of ${sp.get('month_cap', 0):.0f} this month, ${sp.get('week_usd', 0):.2f} of ${sp.get('week_cap', 0):.0f} in the last 7 days",
        f"Live page connected: {'yes' if st.get('relay_connected') else 'no'}",
    ]
    if stuck:
        lines.append(f"Verses the model could not recite correctly after 3 tries (skipped): {stuck}")
    print("\n".join(lines))


if __name__ == "__main__":
    (daily if (len(sys.argv) > 1 and sys.argv[1] == "daily") else watch)()
