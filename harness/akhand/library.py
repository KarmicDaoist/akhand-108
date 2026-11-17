"""Loads and validates the text library (Contract 1)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .config import SEATS, STATIONS
from .verify import normalize

MODES = ("parallel", "relay", "japa", "japa-per-seat")


class LibraryError(Exception):
    pass


@dataclass(frozen=True)
class Verse:
    station: int
    work_id: str
    work_en: str
    n: int
    text: str
    norm: str


@dataclass
class Station:
    index: int
    id: str
    label_en: str
    label_hi: str
    mode: str
    verses: list[Verse]
    seat_verses: dict[int, list[Verse]] = field(default_factory=dict)

    def verses_for(self, seat: int) -> list[Verse]:
        if self.mode == "japa-per-seat":
            return self.seat_verses.get(seat) or self.verses
        return self.verses


@dataclass
class Library:
    stations: list[Station]
    gathering: dict[str, list[Verse]]
    built_at: str


def _verses(raw, station, work_id, work_en, where) -> list[Verse]:
    if not isinstance(raw, list) or not raw:
        raise LibraryError(f"{where}: no verses")
    out = []
    for k, v in enumerate(raw):
        text = v.get("text") if isinstance(v, dict) else None
        if not isinstance(text, str):
            raise LibraryError(f"{where}: verse {k} has no text")
        norm = normalize(text)
        if not norm:
            raise LibraryError(f"{where}: verse {k} is empty after normalisation")
        out.append(Verse(station, work_id, work_en, int(v.get("n", k + 1)), text, norm))
    return out


def load_library(path: Path) -> Library:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise LibraryError(f"library not found at {path} (mount it or set LIBRARY_PATH)")
    except json.JSONDecodeError as e:
        raise LibraryError(f"library is not valid JSON: {e}")
    stations: list[Station] = []
    by_index = {s.get("index"): s for s in data.get("stations", [])}
    for i in range(STATIONS):
        s = by_index.get(i)
        if s is None:
            raise LibraryError(f"station {i} missing")
        mode = s.get("mode")
        if mode not in MODES:
            raise LibraryError(f"station {i}: unknown mode {mode!r}")
        works = s.get("works") or []
        if not works:
            raise LibraryError(f"station {i}: no works")
        verses: list[Verse] = []
        per_seat: dict[int, list[Verse]] = {}
        for w in works:
            wid = w.get("id", "?")
            wen = w.get("title_en") or wid
            vs = _verses(w.get("verses"), i, wid, wen, f"station {i} work {wid}")
            verses += vs
            if w.get("seat") is not None:
                per_seat.setdefault(int(w["seat"]), []).extend(vs)
        if mode == "japa-per-seat" and not all(k in per_seat for k in range(SEATS)):
            raise LibraryError(f"station {i}: japa-per-seat needs a work for every seat 0-8")
        stations.append(Station(i, s.get("id", str(i)), s.get("label_en", ""), s.get("label_hi", ""), mode, verses, per_seat))
    gathering = {}
    for key in ("ganesh", "shiv", "durga"):
        g = (data.get("gathering") or {}).get(key)
        if not g:
            raise LibraryError(f"gathering text {key!r} missing")
        gathering[key] = _verses(g.get("verses"), -1, key, g.get("title_en", key), f"gathering {key}")
    return Library(stations, gathering, str(data.get("built_at", "")))
