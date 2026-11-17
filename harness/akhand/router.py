"""Chooses the provider for each request: the free providers in order, then the paid backup; cooldowns, probes, resting."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import NamedTuple

from .breaker import Breaker

log = logging.getLogger("akhand.router")

LONG_RETRY_S = 120.0  # a 429 asking for more than this puts the provider in cooldown at once
MAX_COOLDOWN_S = 26 * 3600.0
PROBE_RETRY_S = 60.0  # a failed post-cooldown probe waits this long before the next one


class Route(NamedTuple):
    provider: object
    role: str  # "free" | "backup"
    probe: bool


@dataclass
class Health:
    name: str
    role: str
    breaker: Breaker | None = None  # free providers only
    blocked_until: float = 0.0  # monotonic; auth / model gone
    blocked_reason: str | None = None
    cooldown_until: float = 0.0  # wall clock (epoch), persisted in the ledger
    needs_probe: bool = False
    probe_claimed: bool = False
    calls: int = 0
    fails: int = 0
    last_error: str | None = None
    last_ok_wall: float | None = None


class Router:
    def __init__(self, free, backup, budget, clock, auth_rest_s=900.0, ledger=None, breaker_factory=None):
        self.free, self.backup, self.budget, self.clock, self.ledger = list(free), backup, budget, clock, ledger
        self.auth_rest_s = auth_rest_s
        mk = breaker_factory or (lambda: Breaker(clock))
        self.health = {p.name: Health(p.name, "free", mk()) for p in self.free}
        self.health[backup.name] = Health(backup.name, "backup")
        self._free_h = [self.health[p.name] for p in self.free]
        self._backup_h = self.health[backup.name]
        self._load_cooldowns()

    # ---- cooldowns (persisted, so a restart does not hammer a provider that is still cooling down) ----
    def _load_cooldowns(self):
        if self.ledger is None:
            return
        for h in self.health.values():
            try:
                v = float(self.ledger.meta_get(f"cooldown:{h.name}", 0) or 0)
            except (TypeError, ValueError):
                v = 0.0
            if v > 0:
                h.cooldown_until, h.needs_probe, h.blocked_reason = v, True, "cooldown"

    def _save_cooldown(self, h: Health):
        if self.ledger is None:
            return
        try:
            self.ledger.meta_set(f"cooldown:{h.name}", int(h.cooldown_until))
        except Exception:
            log.warning("could not persist the cooldown of %s", h.name)

    def _set_cooldown(self, h: Health, seconds: float, *, extend_only: bool = True):
        until = self.clock.wall() + min(seconds, MAX_COOLDOWN_S)
        h.cooldown_until = max(h.cooldown_until, until) if extend_only else until
        h.needs_probe, h.blocked_reason = True, "cooldown"
        if h.breaker is not None:
            h.breaker.reset()  # the cooldown supersedes it; the probe after it decides
        self._save_cooldown(h)

    def _cooling(self, h: Health) -> bool:
        return self.clock.wall() < h.cooldown_until

    def _blocked(self, h: Health) -> bool:
        return self.clock.mono() < h.blocked_until or self._cooling(h)

    # ---- state -----------------------------------------------------------
    def _free_normal(self, h: Health) -> bool:
        return not self._blocked(h) and not h.needs_probe and not h.breaker.tripped

    def _backup_normal(self) -> bool:
        h = self._backup_h
        return not self._blocked(h) and not h.needs_probe and self.budget.has_room()

    def usable(self) -> bool:
        """Would a normal (non-probe) request be routed somewhere right now?"""
        return any(self._free_normal(h) for h in self._free_h) or self._backup_normal()

    def label(self) -> str:
        """Short name of the model a normal request would use now, or "none"."""
        for h in self._free_h:
            if self._free_normal(h):
                return h.name
        return self._backup_h.name if self._backup_normal() else "none"

    def is_free(self, label: str) -> bool:
        return label in self.health and self.health[label].role == "free"

    def choose(self) -> Route | None:
        by_name = {p.name: p for p in self.free}
        for h in self._free_h:
            if self._free_normal(h):
                return Route(by_name[h.name], "free", False)
            if not self._blocked(h) and not h.probe_claimed and (h.needs_probe or h.breaker.probe_due()):
                h.probe_claimed = True
                return Route(by_name[h.name], "free", True)
        bh = self._backup_h
        if not self._blocked(bh):
            if bh.needs_probe:
                if not bh.probe_claimed and self.budget.has_room():
                    bh.probe_claimed = True
                    return Route(self.backup, "backup", True)
            elif self.budget.has_room():
                return Route(self.backup, "backup", False)
        return None

    def permits(self, route: Route) -> bool:
        """False if the provider was blocked (auth, model gone, cooldown) while this request waited in a queue."""
        return route.probe or not self._blocked(self.health[route.provider.name])

    def release(self, route: Route):
        """The request was abandoned before it produced a verdict."""
        if route.probe:
            self.health[route.provider.name].probe_claimed = False

    def report(self, route: Route, *, ok: bool, kind: str | None = None, retry_after: float | None = None) -> bool:
        """Record the verdict of a request. Returns True if the provider is now in cooldown
        (the caller sends the request's verses to the next provider at once, without backing off)."""
        h = self.health[route.provider.name]
        h.calls += 1
        if route.probe:
            h.probe_claimed = False
        if ok:
            h.last_ok_wall = self.clock.wall()
        else:
            h.fails += 1
            h.last_error = kind
        if kind in ("auth", "model_unavailable"):
            if h.blocked_reason is None or not self._blocked(h):
                log.error("%s provider %s: resting it for %.0fs", kind, h.name, self.auth_rest_s)
            h.blocked_until = self.clock.mono() + self.auth_rest_s
            h.blocked_reason = kind
            h.needs_probe = True
            return False
        if kind == "rate_limit" and retry_after is not None and retry_after > LONG_RETRY_S:
            if not self._cooling(h):
                log.warning("%s asked us to come back in %.0fs: cooling it down, moving its requests to the next provider",
                            h.name, retry_after)
            self._set_cooldown(h, retry_after)
            return True
        if route.probe:
            if not ok and h.cooldown_until > 0:
                self._set_cooldown(h, PROBE_RETRY_S, extend_only=False)  # still failing after its cooldown: probe again in a minute
                return True
            h.needs_probe = False
            h.blocked_reason = None
            if ok and h.cooldown_until > 0:
                h.cooldown_until = 0.0
                self._save_cooldown(h)
                log.warning("%s is back after its cooldown", h.name)
            if h.breaker is not None and h.breaker.tripped:
                h.breaker.probe_result(ok)
                if ok:
                    log.warning("%s recovered", h.name)
            return False
        if ok:
            h.blocked_reason = None
        if h.breaker is not None and h.breaker.record(ok):
            log.warning("%s failed on most calls in the last %.0fs; leaving it", h.name, h.breaker.window_s)
        return False

    def provider_health(self) -> dict:
        out = {}
        now = self.clock.wall()
        for h in self.health.values():
            if self._cooling(h) or (h.needs_probe and h.cooldown_until > 0):
                state = "cooldown"
            elif self._blocked(h) or (h.needs_probe and h.blocked_reason):
                state = "auth"
            elif h.breaker is not None and h.breaker.tripped:
                state = "failing"
            elif h.role == "backup" and not self.budget.has_room():
                state = "budget-dry"
            else:
                state = "ok"
            out[h.name] = {"state": state, "calls": h.calls, "fails": h.fails, "last_error": h.last_error,
                           "cooldown_until": int(h.cooldown_until) if h.cooldown_until > now else None}
        return out
