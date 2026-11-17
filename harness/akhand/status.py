"""The status document: what `ctl status --json` prints and what is exported for Hermes."""
from __future__ import annotations

import json
import time
from datetime import date, datetime

from .sankalpa import for_config as sankalpa_for

HEARTBEAT_STALE_S = 20.0


def local_today(cfg, wall: float, ledger) -> date:
    d = datetime.fromtimestamp(wall, cfg.tzinfo).date()
    hw = ledger.meta_get("hw_day")
    if hw:
        h = date.fromisoformat(hw)
        if 0 < (h - d).days <= 2:
            return h
    return d


def compose_status(ledger, cfg, wall: float | None = None) -> dict:
    wall = time.time() if wall is None else wall
    today = local_today(cfg, wall, ledger)
    units, corrections = ledger.day_row(today.isoformat())
    total = ledger.total_units()
    ms = datetime(today.year, today.month, 1, tzinfo=cfg.tzinfo)
    nxt = datetime(today.year + (today.month == 12), 1 if today.month == 12 else today.month + 1, 1, tzinfo=cfg.tzinfo)
    month = ledger.spend_between(int(ms.timestamp() // 60), int(nxt.timestamp() // 60))
    week = float(sum(ledger.spend_minutes_since(int(wall // 60) - 7 * 24 * 60).values()))
    rt = None
    raw = ledger.control_get("runtime")
    if raw:
        try:
            rt = json.loads(raw)
        except ValueError:
            rt = None
    paused = ledger.control_get("paused") == "1"
    lu = ledger.last_unit_wall()
    since = max(0.0, wall - lu) if lu else None
    hb_age = max(0.0, wall - rt["ts"]) if rt else None
    st = {
        "mode": rt["mode"] if rt else "down",
        "provider": rt["provider"] if rt else "none",
        "model": rt.get("model") if rt else None,
        "providers": rt["providers"] if rt else {},
        "paused": paused,
        "today": {"units": units, "malas": units // 108, "corrections": corrections},
        "all_time": {"units": total, "malas": total // 108},
        "spend": {"month_usd": round(month, 6), "week_usd": round(week, 6), "month_cap": cfg.month_usd, "week_cap": cfg.week_usd},
        "seconds_since_last_unit": None if since is None else round(since, 1),
        "stuck_verses": {"count": ledger.stuck_count(), "recent": ledger.stuck_recent(5)},
        "relay_configured": bool(rt["relay_configured"]) if rt else bool(cfg.relay_url),
        "relay_connected": bool(rt["relay_connected"]) if rt else False,
        "heartbeat_age_s": None if hb_age is None else round(hb_age, 1),
        "pid": rt.get("pid") if rt else None,
        "uptime_s": round(wall - rt["started"], 1) if rt else None,
        "sankalpa": sankalpa_for(cfg).status(today),
        "exported_at": int(wall),
    }
    st["health"] = judge(st, rt)
    st["exit_code"] = {"healthy": 0, "degraded": 1, "down": 2}[st["health"]]
    return st


def judge(st: dict, rt) -> str:
    if rt is None or st["heartbeat_age_s"] is None or st["heartbeat_age_s"] > HEARTBEAT_STALE_S:
        return "down"
    if st["mode"] == "resting" and not st["paused"]:
        return "down"
    if st["paused"] or st["provider"] != "iceman":
        return "degraded"
    if st["relay_configured"] and not st["relay_connected"]:
        return "degraded"
    return "healthy"
