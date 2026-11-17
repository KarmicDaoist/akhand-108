"""The full sankalpa: an editable template filled with the day's panchang and the yajamana's details.

Mode `simple` (default) is the old single fixed sentence. Mode `full` renders `sankalpa/template.txt`, one clause
per line, one line = one verse at the gathering. A full sankalpa is never recited half-filled: any problem found at
startup (missing value, unknown placeholder, bad coordinates, unreadable template, a line that is too long) puts the
harness back on `simple` and says why in the log and in `ctl status`.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date as Date, datetime, tzinfo
from pathlib import Path

from . import panchang as pj
from .verify import normalize

log = logging.getLogger("akhand")

DEFAULT_TEMPLATE = Path(__file__).resolve().parent.parent / "sankalpa" / "template.txt"
MAX_LINE_CHARS = 320
MODES = ("simple", "full")

PANCHANG_FIELDS = ("samvatsara", "vikram_year", "ayana", "ritu", "masa", "paksha", "tithi", "vara", "nakshatra")
TEXT_FIELDS = ("gotra", "name", "city", "state", "purpose", "act")  # supplied ready-inflected by the operator
PLACEHOLDERS = PANCHANG_FIELDS + TEXT_FIELDS
_PLACEHOLDER = re.compile(r"\{([^{}]*)\}")


@dataclass(frozen=True)
class SankalpaSettings:
    mode: str = "simple"
    gotra: str = ""
    name: str = ""
    city: str = ""
    state: str = ""
    purpose: str = ""
    act: str = ""
    lat: str = ""
    lon: str = ""
    masa_system: str = "purnimanta"
    samvatsara: str = "north"
    ayana: str = "drik"
    template_path: str = ""

    @classmethod
    def from_env(cls, env) -> "SankalpaSettings":
        def s(key, default=""):
            v = env.get(key)
            return default if v is None or v.strip() == "" else v.strip()

        return cls(
            mode=s("SANKALPA_MODE", "simple").lower(),
            gotra=s("SANKALPA_GOTRA_TEXT"), name=s("SANKALPA_NAME_TEXT"), city=s("SANKALPA_CITY_TEXT"),
            state=s("SANKALPA_STATE_TEXT"), purpose=s("SANKALPA_PURPOSE_TEXT"), act=s("SANKALPA_ACT_TEXT"),
            lat=s("PANCHANG_LAT"), lon=s("PANCHANG_LON"),
            masa_system=s("PANCHANG_MASA_SYSTEM", "purnimanta").lower(),
            samvatsara=s("PANCHANG_SAMVATSARA", "north").lower(),
            ayana=s("PANCHANG_AYANA", "drik").lower(),
            template_path=s("SANKALPA_TEMPLATE_PATH"))

    def text_value(self, field: str) -> str:
        return getattr(self, field)


def load_template(path: Path) -> list[str]:
    """Clause lines of the template: UTF-8, blank lines and `#` comment lines skipped."""
    text = Path(path).read_text(encoding="utf-8-sig")
    return [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]


def _template_fields(lines: list[str]) -> set[str]:
    return {m.group(1) for ln in lines for m in _PLACEHOLDER.finditer(ln)}


def _coords(st: SankalpaSettings) -> tuple[float, float]:
    lat, lon = float(st.lat), float(st.lon)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("out of range")
    return lat, lon


def check(st: SankalpaSettings, tz: tzinfo, today: Date | None = None) -> list[str]:
    """Every problem that would stop a full sankalpa from being recited correctly; [] means it is ready.

    Includes a trial render for `today`, so a lunar-month bug or an over-long line shows up at startup."""
    errors: list[str] = []
    path = Path(st.template_path) if st.template_path else DEFAULT_TEMPLATE
    lines: list[str] = []
    try:
        lines = load_template(path)
        if not lines:
            errors.append(f"template {path} has no lines")
    except (OSError, UnicodeDecodeError) as e:
        errors.append(f"template {path} cannot be read: {e}")
    fields = _template_fields(lines)
    for f in sorted(fields - set(PLACEHOLDERS)):
        errors.append(f"unknown placeholder {{{f}}} in template")
    for f in TEXT_FIELDS:
        if f in fields and not st.text_value(f):
            errors.append(f"SANKALPA_{f.upper()}_TEXT is required by the template but empty")
    try:
        _coords(st)
    except ValueError:
        errors.append(f"PANCHANG_LAT/PANCHANG_LON must be set to numbers (got {st.lat!r}, {st.lon!r})")
    for name, val, allowed in (("PANCHANG_MASA_SYSTEM", st.masa_system, pj.MASA_SYSTEMS),
                               ("PANCHANG_SAMVATSARA", st.samvatsara, pj.SAMVATSARA_SYSTEMS),
                               ("PANCHANG_AYANA", st.ayana, pj.AYANA_SYSTEMS)):
        if val not in allowed:
            errors.append(f"{name}={val!r} must be one of {', '.join(allowed)}")
    if errors:
        return errors
    try:
        out, _ = render(st, today or datetime.now(tz).date(), tz)
    except Exception as e:  # trial render must never crash startup
        return [f"trial render failed: {type(e).__name__}: {e}"]
    for i, ln in enumerate(out, 1):
        if len(ln) > MAX_LINE_CHARS:
            errors.append(f"template line {i} renders to {len(ln)} characters (limit {MAX_LINE_CHARS})")
        if "{" in ln or "}" in ln:
            errors.append(f"template line {i} still has braces after rendering")
        if not normalize(ln):
            errors.append(f"template line {i} is empty after normalisation")
    return errors


def render(st: SankalpaSettings, day: Date, tz: tzinfo) -> tuple[list[str], pj.Panchang]:
    """The sankalpa lines for `day` and the panchang they were built from. Raises on any problem."""
    path = Path(st.template_path) if st.template_path else DEFAULT_TEMPLATE
    lines = load_template(path)
    lat, lon = _coords(st)
    p = pj.compute(day, lat, lon, tz, masa_system=st.masa_system, samvatsara_system=st.samvatsara,
                   ayana_system=st.ayana)
    values = dict(p.devanagari())
    values.update({f: st.text_value(f) for f in TEXT_FIELDS})

    def fill(m):
        key = m.group(1)
        if key not in values or not values[key]:
            raise KeyError(f"placeholder {{{key}}} has no value")
        return values[key]

    out = [re.sub(r"\s+", " ", _PLACEHOLDER.sub(fill, ln)).strip() for ln in lines]
    return out, p


class Sankalpa:
    """What the harness uses: effective mode, the status block and the lines for a day."""

    def __init__(self, st: SankalpaSettings, tz: tzinfo, today: Date | None = None):
        self.settings, self.tz = st, tz
        self.requested = st.mode
        self.errors: list[str] = []
        if st.mode not in MODES:
            self.errors = [f"SANKALPA_MODE={st.mode!r} must be simple or full"]
        elif st.mode == "full":
            self.errors = check(st, tz, today)
        self.mode = "full" if st.mode == "full" and not self.errors else "simple"
        self._cache: dict[Date, tuple[list[str], pj.Panchang]] = {}

    def log_startup(self):
        if self.mode == "full":
            log.info("sankalpa: full mode (%s)", self.settings.template_path or DEFAULT_TEMPLATE)
        elif self.errors:
            log.error("sankalpa: falling back to SIMPLE mode, full sankalpa not used: %s", "; ".join(self.errors))

    def render(self, day: Date) -> tuple[list[str], pj.Panchang]:
        if day not in self._cache:
            if len(self._cache) > 8:
                self._cache.clear()
            self._cache[day] = render(self.settings, day, self.tz)
        return self._cache[day]

    def status(self, day: Date) -> dict:
        st = {"mode": self.mode, "date": day.isoformat(), "tithi": None, "vara": None, "nakshatra": None,
              "masa": None, "paksha": None, "near_boundary": None}
        if self.requested != self.mode:
            st["requested"] = self.requested
            st["error"] = "; ".join(self.errors)
        if self.mode == "full":
            try:
                _, p = self.render(day)
                pl = p.plain()
                st.update(tithi=pl["tithi"], vara=pl["vara"], nakshatra=pl["nakshatra"], masa=pl["masa"],
                          paksha=pl["paksha"], near_boundary=p.near_boundary)
            except Exception as e:
                st["error"] = f"render failed: {type(e).__name__}: {e}"
        return st


_RUNTIME: dict = {}


def for_config(cfg) -> Sankalpa:
    """One validated Sankalpa per distinct settings (ctl status calls compose_status often)."""
    key = (cfg.sankalpa, cfg.tz)
    if key not in _RUNTIME:
        _RUNTIME.clear()
        _RUNTIME[key] = Sankalpa(cfg.sankalpa, cfg.tzinfo)
    return _RUNTIME[key]
