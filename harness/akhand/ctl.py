"""Control from outside the process: python -m akhand.ctl status [--json] | pause | resume | stop | sankalpa [--date YYYY-MM-DD]"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime

from .config import Config
from .ledger import Ledger
from . import sankalpa as sk
from .status import compose_status


def _open(cfg):
    return Ledger(cfg.data_dir / "akhand.db", create=False)


def _print_sankalpa(cfg, day: date) -> int:
    """Preview the full sankalpa for a date, whatever SANKALPA_MODE says; needs no running process and no ledger."""
    st = cfg.sankalpa
    errors = sk.check(st, cfg.tzinfo, day)
    print(f"configured mode: {st.mode}; full sankalpa {'ready' if not errors else 'NOT usable'}")
    if errors:
        for e in errors:
            print(f"  problem: {e}")
        return 1
    lines, p = sk.render(st, day, cfg.tzinfo)
    pl = p.plain()
    print(f"\nSankalpa for {day.isoformat()} ({pl['vara']}), place {p.lat:.4f}, {p.lon:.4f}:\n")
    for ln in lines:
        print(ln)
    print(f"\nPanchang at sunrise ({p.sunrise.strftime('%H:%M')} {cfg.tz}):")
    print(f"  vara        {pl['vara']}")
    print(f"  tithi       {pl['paksha']} {pl['tithi']} (tithi {p.tithi} of 30)")
    print(f"  nakshatra   {pl['nakshatra']} ({p.nakshatra} of 27)")
    print(f"  masa        {pl['masa']} ({p.masa_system}); amanta {pl['masa_amanta']}, purnimanta {pl['masa_purnimanta']}")
    print(f"  ritu        {pl['ritu']}")
    print(f"  ayana       {pl['ayana']} ({p.ayana_system})")
    print(f"  vikram year {p.vikram_year}; shaka year {p.shaka_year}")
    print(f"  samvatsara  {pl['samvatsara']} ({p.samvatsara_system})")
    print(f"  near boundary: {'YES' if p.near_boundary else 'no'} (tithi change {p.minutes_to_tithi_change} min away, "
          f"nakshatra change {p.minutes_to_nakshatra_change} min away)")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="akhand.ctl")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("status")
    s.add_argument("--json", action="store_true")
    sa = sub.add_parser("sankalpa", help="print the rendered full sankalpa and panchang for a date")
    sa.add_argument("--date", help="YYYY-MM-DD, default today in TZ")
    for c in ("pause", "resume", "stop"):
        sub.add_parser(c)
    args = ap.parse_args(argv)
    cfg = Config.from_env()
    if args.cmd == "sankalpa":
        try:
            day = date.fromisoformat(args.date) if args.date else datetime.now(cfg.tzinfo).date()
        except ValueError:
            print(f"--date {args.date!r} must be YYYY-MM-DD", file=sys.stderr)
            return 2
        return _print_sankalpa(cfg, day)
    try:
        ledger = _open(cfg)
    except Exception as e:
        if args.cmd == "status":
            doc = {"mode": "down", "health": "down", "exit_code": 2, "error": f"no ledger: {e}"}
            print(json.dumps(doc) if args.json else f"down: no ledger ({e})")
            return 2
        print(f"no ledger at {cfg.data_dir}: {e}", file=sys.stderr)
        return 2
    try:
        if args.cmd == "status":
            st = compose_status(ledger, cfg)
            if args.json:
                print(json.dumps(st, indent=2))
            else:
                print(f"{st['health']}: mode={st['mode']} provider={st['provider']} today={st['today']['units']} units "
                      f"({st['today']['malas']} malas, {st['today']['corrections']} corrections) "
                      f"last unit {st['seconds_since_last_unit']}s ago, relay={'up' if st['relay_connected'] else 'down'}, "
                      f"stuck={st['stuck_verses']['count']}, month ${st['spend']['month_usd']}, week ${st['spend']['week_usd']}")
            return st["exit_code"]
        if args.cmd in ("pause", "resume"):
            ledger.control_set("paused", "1" if args.cmd == "pause" else "0")
            print(f"{args.cmd} requested (applied within a second)")
        else:
            ledger.control_set("stop", "1")
            print("stop requested")
        return 0
    finally:
        ledger.close()


if __name__ == "__main__":
    sys.exit(main())
