"""Strict validators for Contract 2 messages (used by tests and the soak run)."""
from __future__ import annotations

MODES = ("praying", "gathering", "resting")
PROVIDERS = ("iceman", "muse", "none")


def _int(x):
    return isinstance(x, int) and not isinstance(x, bool)


def validate(msg) -> list[str]:
    errs: list[str] = []
    if not isinstance(msg, dict):
        return ["not an object"]
    t = msg.get("t")
    if t == "batch":
        if set(msg) != {"t", "ts", "ev"}:
            errs.append(f"batch keys {sorted(msg)}")
        if not _int(msg.get("ts")):
            errs.append("ts not int")
        ev = msg.get("ev")
        if not isinstance(ev, list) or not ev:
            errs.append("ev must be a non-empty list")
        else:
            for p in ev:
                if not (isinstance(p, list) and len(p) == 2 and _int(p[0]) and _int(p[1]) and 0 <= p[0] <= 107 and p[1] >= 1):
                    errs.append(f"bad pair {p!r}")
    elif t == "state":
        need = {"t", "ts", "mode", "provider", "name", "day", "streak_days", "today", "all_time", "stations", "voices", "relay_holders"}
        if not need <= set(msg):
            errs.append(f"missing {sorted(need - set(msg))}")
            return errs
        if not _int(msg["ts"]):
            errs.append("ts")
        if msg["mode"] not in MODES:
            errs.append("mode")
        if msg["provider"] not in PROVIDERS:
            errs.append("provider")
        if "model" in msg and not isinstance(msg["model"], str):
            errs.append("model")
        if not isinstance(msg["name"], str):
            errs.append("name")
        for k in ("day", "streak_days"):
            if not _int(msg[k]) or msg[k] < 0:
                errs.append(k)
        td = msg["today"]
        if not (isinstance(td, dict) and all(_int(td.get(k)) for k in ("units", "malas", "corrections"))):
            errs.append("today")
        elif td["malas"] != td["units"] // 108:
            errs.append("today.malas != floor(units/108)")
        at = msg["all_time"]
        if not (isinstance(at, dict) and all(_int(at.get(k)) for k in ("units", "malas"))):
            errs.append("all_time")
        elif at["malas"] != at["units"] // 108:
            errs.append("all_time.malas != floor(units/108)")
        st = msg["stations"]
        if not (isinstance(st, list) and len(st) == 12 and all(
                isinstance(s, dict) and _int(s.get("units_today")) and _int(s.get("active")) and 0 <= s["active"] <= 9 for s in st)):
            errs.append("stations")
        vo = msg["voices"]
        if not (isinstance(vo, list) and len(vo) <= 12):
            errs.append("voices")
        else:
            seen = set()
            for v in vo:
                if not (isinstance(v, dict) and _int(v.get("station")) and 0 <= v["station"] <= 11 and _int(v.get("agent"))
                        and 0 <= v["agent"] <= 107 and isinstance(v.get("work_en"), str) and isinstance(v.get("text"), str)
                        and len(v["text"].split("\n")) <= 2 and v["station"] not in seen):
                    errs.append(f"voice {v!r}")
                else:
                    seen.add(v["station"])
        rh = msg["relay_holders"]
        if not (isinstance(rh, dict) and all(isinstance(k, str) and k.isdigit() and 0 <= int(k) <= 11 and _int(v) and 0 <= v <= 8 for k, v in rh.items())):
            errs.append("relay_holders")
    else:
        errs.append(f"unknown t {t!r}")
    return errs
