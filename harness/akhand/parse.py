"""Incremental parser: turns a streamed reply into verified verses, ignoring everything else."""
from __future__ import annotations

from difflib import SequenceMatcher

from .verify import normalize

ATTEMPT_RATIO = 0.6


class VerseStream:
    """Feed text as it streams. feed()/finish() return the indices (into `expected`) that
    were just verified, always in order. Extra lines, numbering and commentary are noise."""

    def __init__(self, expected_norms: list[str]):
        self.exp = list(expected_norms)
        self.i = 0
        self.mismatched = False
        self.noise_blocks = 0
        self.chars = 0
        self._buf = ""
        self._lines: list[str] = []

    @property
    def done(self) -> bool:
        return self.i >= len(self.exp) or self.mismatched

    def feed(self, text: str) -> list[int]:
        self.chars += len(text.strip())
        self._buf += text.replace("\r", "")
        out: list[int] = []
        while "\n" in self._buf and not self.done:
            line, self._buf = self._buf.split("\n", 1)
            out += self._line(line)
        return out

    def finish(self, clean: bool) -> list[int]:
        """End of stream. clean=False (cut off): a trailing partial block is dropped, not
        judged; an exact match is still accepted."""
        out: list[int] = []
        if self._buf and not self.done:
            line, self._buf = self._buf, ""
            out += self._line(line)
        if clean and not self.done:
            self._boundary()
        return out

    def _line(self, line: str) -> list[int]:
        if self.done:
            return []
        if not line.strip():
            self._boundary()
            return []
        self._lines.append(line)
        for start in range(len(self._lines)):
            if normalize("\n".join(self._lines[start:])) == self.exp[self.i]:
                self._lines = []
                self.i += 1
                return [self.i - 1]
        return []

    def _boundary(self) -> None:
        if not self._lines or self.done:
            self._lines = []
            return
        nb = normalize("\n".join(self._lines))
        if not nb:
            self._lines = []
        elif self.exp[self.i].startswith(nb):
            pass  # first part of a verse split by a blank line: keep holding
        else:
            if SequenceMatcher(None, nb, self.exp[self.i]).ratio() >= ATTEMPT_RATIO:
                self.mismatched = True
            else:
                self.noise_blocks += 1
            self._lines = []
