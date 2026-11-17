"""Verse verification: normalise both sides, then exact equality."""
from __future__ import annotations

import unicodedata


def normalize(s: str) -> str:
    """NFC, then drop whitespace, format chars (ZWJ/ZWNJ), punctuation (incl. dandas),
    symbols and digits (Devanagari and ASCII). Letters and combining marks are kept, so a
    changed vowel sign, nukta, anusvara or candrabindu is a different word."""
    out = []
    for ch in unicodedata.normalize("NFC", s):
        if unicodedata.category(ch)[0] in "ZCPSN":
            continue
        out.append(ch)
    return unicodedata.normalize("NFC", "".join(out))


def same_verse(got: str, expected_norm: str) -> bool:
    return normalize(got) == expected_norm
