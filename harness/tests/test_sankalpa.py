"""Full sankalpa mode: a missing value or unknown placeholder falls back to simple, never a half-filled sankalpa."""
from datetime import date

from akhand.config import Config
from akhand.sankalpa import Sankalpa

FULL = {
    "SANKALPA_MODE": "full", "PANCHANG_LAT": "21.2514", "PANCHANG_LON": "81.6296", "TZ": "Asia/Kolkata",
    "SANKALPA_GOTRA_TEXT": "कश्यपगोत्रोत्पन्नस्य", "SANKALPA_NAME_TEXT": "देवदत्तशर्मणः",
    "SANKALPA_CITY_TEXT": "काशीनगरे", "SANKALPA_STATE_TEXT": "उत्तरप्रदेशे",
    "SANKALPA_PURPOSE_TEXT": "आत्मकल्याणार्थं", "SANKALPA_ACT_TEXT": "अखण्डजपपाठं",
}


def _sankalpa(env):
    cfg = Config.from_env(env)
    return Sankalpa(cfg.sankalpa, cfg.tzinfo, date(2026, 11, 14))


def test_full_mode_falls_back_to_simple_when_something_is_missing(tmp_path):
    ok = _sankalpa(FULL)
    assert ok.mode == "full" and len(ok.render(date(2026, 11, 14))[0]) == 6

    missing = _sankalpa({k: v for k, v in FULL.items() if k != "SANKALPA_GOTRA_TEXT"})
    assert missing.mode == "simple" and "SANKALPA_GOTRA_TEXT" in missing.errors[0]
    st = missing.status(date(2026, 11, 14))
    assert st["mode"] == "simple" and st["requested"] == "full" and st["tithi"] is None

    bad = tmp_path / "t.txt"
    bad.write_text("ॐ {tithi} {bogus}\n", encoding="utf-8")
    assert _sankalpa({**FULL, "SANKALPA_TEMPLATE_PATH": str(bad)}).mode == "simple"
