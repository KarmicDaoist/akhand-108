"""The harness: 108 agent tasks, one ledger, one relay client."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from datetime import date as _date
from pathlib import Path

from .breaker import Breaker
from .budget import Budget, BudgetDry
from .clock import Calendar, Clock
from .config import AGENTS, SEATS, STATIONS, Config
from .events import Events, Pacer
from .ledger import Ledger, LedgerBusy
from .library import load_library
from .lock import InstanceLock
from .modes import WAIT, Directory
from .provider import ProviderError, build_providers, est_tokens
from .relay import RelayClient
from .parse import VerseStream
from .router import Router
from .sankalpa import Sankalpa
from .status import compose_status

log = logging.getLogger("akhand")

SYSTEM = ("You are one of 108 voices reciting Hindu prayers in Devanagari as a devotional offering. "
          "Sankalpa: {sankalpa} You recite with the book open: copy the given text exactly, "
          "without changing, adding or leaving out any letter.")
USER_HEAD = ("Recite these {n} verses exactly as written. Output only the verses, one verse per block, "
             "blocks separated by one blank line. Keep line breaks inside a verse. "
             "No numbering, no translation, no commentary, nothing else.\n\n")
EXIT_WATCHDOG = 4


class _RT:
    """State of one in-flight request."""

    def __init__(self, plan, prov):
        self.plan, self.prov = plan, prov
        self.parser = VerseStream([v.norm for v in plan.verses])
        self.committed = 0
        self.chars = 0
        self.finished = False


class Harness:
    def __init__(self, cfg: Config, *, free=None, backup=None, clock: Clock | None = None):
        self.cfg = cfg
        self.clock = clock or Clock()
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        self.lock = InstanceLock(cfg.data_dir / "akhand.lock")
        try:
            self.ledger = Ledger(cfg.data_dir / "akhand.db")
            self.calendar = Calendar(cfg.tzinfo, self.clock, self.ledger)
            self.library = load_library(cfg.library_path)
            if free is None or backup is None:
                free, backup = build_providers(cfg)
            self.free, self.backup = list(free), backup
            self.primary = self.free[0]
            self.verses_per_call = {m.name: m.verses_per_call for m in cfg.free}
            self.verses_per_call[backup.name] = cfg.backup_verses_per_call
            self.directory = Directory(self.library, cfg, self.ledger, cfg.sankalpa_line())
            self.sankalpa = Sankalpa(cfg.sankalpa, cfg.tzinfo)
            self.sankalpa.log_startup()
            self.budget = Budget(self.ledger, self.clock, self.calendar, cfg.month_usd, cfg.week_usd,
                                 paced=cfg.backup_paced, min_est=max(1e-6, getattr(backup, "reserve_usd", 0.0003) or 0.0003))
            self.router = Router(self.free, backup, self.budget, self.clock, cfg.auth_rest_s, ledger=self.ledger,
                                 breaker_factory=lambda: Breaker(self.clock, cfg.breaker_window_s, cfg.breaker_min_calls, cfg.probe_interval_s))
        except BaseException:
            self.lock.release()
            raise
        self.events = Events(self.clock)
        self.pacer = Pacer(cfg.verses_per_hour, self.clock)
        self.sems = {"free": asyncio.Semaphore(cfg.max_concurrency), "backup": asyncio.Semaphore(cfg.max_concurrency)}
        self.relay = RelayClient(cfg.relay_url, cfg.relay_token, self.events, self.state_message, self.clock) if cfg.relay_url else None
        self.phase = "praying"
        self.gather_day: str | None = None
        self.paused = self.ledger.control_get("paused") == "1"
        self.stopping = False
        self.exit_code = 0
        self._stop_evt = asyncio.Event()
        self.tasks: list[asyncio.Task] = []
        self.voices: dict[int, tuple[int, str, str]] = {}
        self.inflight = [False] * AGENTS
        self.active_until = [0.0] * AGENTS
        self.last_unit_mono: float | None = None
        self.started_mono = self.clock.mono()
        self.started_wall = self.clock.wall()
        self.fail_counts: dict = {}
        self.agent_fails = [0] * AGENTS
        self._warned: dict[str, float] = {}
        self._export_warned = False

    # ---- logging helper ------------------------------------------------
    def _warn(self, key: str, msg: str, *args, every: float = 30.0):
        now = time.monotonic()
        if now - self._warned.get(key, -1e9) >= every:
            self._warned[key] = now
            log.warning(msg, *args)

    # ---- lifecycle -----------------------------------------------------
    async def start(self):
        try:
            self.ledger.control_set("stop", "0")
        except LedgerBusy:
            pass
        loop = asyncio.get_running_loop()
        mk = lambda c, name: loop.create_task(c, name=name)
        self.tasks = [mk(self._agent_loop(a), f"agent-{a}") for a in range(AGENTS)]
        self.tasks += [mk(self._ticker(), "ticker"), mk(self._control_loop(), "control"), mk(self._gathering_loop(), "gathering"),
                       mk(self._summary_loop(), "summary")]
        if self.relay:
            self.tasks.append(mk(self.relay.run(), "relay"))
        log.info("akhand started: %d agents, tz=%s, gathering %s, provider=%s", AGENTS, self.cfg.tz,
                 self.cfg.gathering_time.strftime("%H:%M"), self.cfg.provider)

    def request_stop(self):
        self._stop_evt.set()

    async def run(self) -> int:
        await self.start()
        await self._stop_evt.wait()
        await self.stop()
        return self.exit_code

    async def stop(self):
        self.stopping = True
        others = [t for t in self.tasks if t.get_name() != "relay"]
        for t in others:
            t.cancel()
        await asyncio.gather(*others, return_exceptions=True)
        self.events.tick()  # the last partial second goes out before the relay task is stopped
        if self.relay is not None and self.relay.connected:
            try:
                await asyncio.wait_for(self.relay.flush(), 2.0)
            except Exception:
                pass
        for t in self.tasks:
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        try:
            self.ledger.control_set("runtime", "")
        except Exception:
            pass
        for p in (*self.free, self.backup):
            try:
                await p.aclose()
            except Exception:
                pass
        self.ledger.close()
        self.lock.release()
        log.info("akhand stopped (exit %d)", self.exit_code)

    async def kill(self):
        """Test hook: die like a killed process: nothing flushed, nothing recorded."""
        self.stopping = True
        for t in self.tasks:
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.ledger.close()
        self.lock.release()

    # ---- modes and state ----------------------------------------------
    def intent(self) -> str:
        if self.paused or not self.router.usable():
            return "resting"
        return "gathering" if self.phase == "gathering" else "praying"

    def silence_s(self) -> float:
        return self.clock.mono() - (self.last_unit_mono if self.last_unit_mono is not None else self.started_mono)

    def mode(self) -> str:
        m = self.intent()
        if m != "resting" and self.silence_s() > self.cfg.resting_after_s:
            return "resting"  # nothing has actually been recited for a while: say so
        return m

    def state_message(self) -> dict:
        mono = self.clock.mono()
        mode = self.mode()
        day = self.calendar.today()
        d_units, d_corr = self.ledger.day_row(day.isoformat())
        total = self.ledger.total_units()
        su = self.ledger.station_units(day.isoformat())
        dnum, streak = self.ledger.day_and_streak(day)
        active = [0] * STATIONS
        if mode != "resting":
            for a in range(AGENTS):
                if self.inflight[a] or self.active_until[a] > mono:
                    active[a // SEATS] += 1
        return {
            "t": "state", "ts": int(self.clock.wall() * 1000), "mode": mode,
            **self._provider_fields(mode),
            "name": self.cfg.sankalpa_name, "day": dnum, "streak_days": streak,
            "today": {"units": d_units, "malas": d_units // 108, "corrections": d_corr},
            "all_time": {"units": total, "malas": total // 108},
            "stations": [{"units_today": su.get(s, 0), "active": active[s]} for s in range(STATIONS)],
            "voices": [{"station": s, "agent": a, "work_en": w, "text": t} for s, (a, w, t) in sorted(self.voices.items())],
            "relay_holders": self.directory.relay_holders(),
        }

    def _provider_fields(self, mode: str) -> dict:
        """Contract 2 `provider` stays iceman (any free model) / muse (paid) / none; `model` names the one in use."""
        label = self.router.label() if mode != "resting" else "none"
        if label == "none":
            return {"provider": "none"}
        return {"provider": "iceman" if self.router.is_free(label) else "muse", "model": label}

    def runtime_status(self) -> dict:
        return {
            "ts": self.clock.wall(), "pid": os.getpid(), "started": self.started_wall, "mode": self.mode(),
            **self._provider_fields(self.mode()),
            "providers": self.router.provider_health(), "paused": self.paused,
            "relay_configured": self.relay is not None, "relay_connected": bool(self.relay and self.relay.connected),
            "phase": self.phase, "intent": self.intent(),
        }

    # ---- agents --------------------------------------------------------
    async def _agent_loop(self, a: int):
        while not self.stopping:
            try:
                await self._step(a)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("agent %d crashed; continuing", a)
                await asyncio.sleep(5)

    async def _step(self, a: int):
        if self.paused:
            await asyncio.sleep(1)
            return
        await self.pacer.wait(a)
        if self.paused:
            return
        day = self.gather_day if self.phase == "gathering" and self.gather_day else self.calendar.today_iso()
        plan = self.directory.plan(a, self.phase, day)
        if plan is None:
            await asyncio.sleep(1)
            return
        if plan is WAIT:
            await self.directory.wait_turn(a // SEATS, 3.0)
            return
        await self._request(a, plan)

    def _build_request(self, a, plan):
        from .provider import Request

        sys = SYSTEM.format(sankalpa=self.cfg.sankalpa_line())
        user = USER_HEAD.format(n=len(plan.verses)) + "\n\n".join(v.text for v in plan.verses)
        chars = sum(len(v.text) for v in plan.verses)
        return Request(system=sys, user=user, max_tokens=min(8192, int(chars * 2) + 1024), session=f"akhand-108-agent-{a:03d}",
                       verses=tuple(v.text for v in plan.verses), meta={"agent": a, "station": plan.station})

    async def _request(self, a: int, plan):
        route = self.router.choose()
        if route is None:
            await asyncio.sleep(random.uniform(2, 5))
            return
        prov, role = route.provider, route.role
        limit = self.verses_per_call.get(prov.name, self.cfg.verses_per_call)
        if plan.kind not in ("japa", "japa-per-seat") and len(plan.verses) > limit:
            plan.verses = plan.verses[:max(1, limit)]
        req = self._build_request(a, plan)
        res = None
        if role == "backup":
            try:
                res = await self.budget.acquire(prov.name, max(prov.reserve_usd, 1e-6))
            except BudgetDry:
                self.router.release(route)
                await asyncio.sleep(random.uniform(5, 15))
                return
        rt = _RT(plan, prov)
        comp = err = None
        skipped = False
        started = self.clock.mono()
        self.inflight[a] = True
        try:
            try:
                async with self.sems[role]:
                    if not self.router.permits(route):
                        skipped = True
                    else:
                        comp = await prov.complete(req, lambda t: self._on_text(a, rt, t))
            except ProviderError as e:
                err = e
            except LedgerBusy as e:
                err = ProviderError("busy", str(e))
            clean = err is None and comp is not None and comp.finish_reason != "length"
            if err is None or err.kind != "busy":
                try:
                    for i in rt.parser.finish(clean):
                        self._commit(a, rt, i)
                except LedgerBusy as e:
                    err = ProviderError("busy", str(e))
        except asyncio.CancelledError:
            if res is not None:
                self.budget.settle(res, self._actual_cost(prov, req, None, ProviderError("timeout", "cancelled"), rt))
            self.router.release(route)
            raise
        finally:
            self.inflight[a] = False
        if skipped:  # provider was blocked while this request queued: no call was made
            if res is not None:
                self.budget.settle(res, 0.0)
            self.router.release(route)
            self.pacer.hold(a, random.uniform(0.1, 0.6))
            return
        if err is None and rt.chars == 0:
            err = ProviderError("malformed", "empty reply")
        if res is not None:
            self.budget.settle(res, self._actual_cost(prov, req, comp, err, rt))
        progress = rt.committed > 0
        kind = err.kind if err else None
        cooled = self.router.report(route, ok=progress and kind not in ("auth", "model_unavailable"), kind=kind,
                                    retry_after=err.retry_after if err else None)
        failed_verse = None
        if err is None and rt.committed < len(plan.verses) and (rt.parser.mismatched or clean):
            failed_verse = rt.committed
        if failed_verse is not None:
            self._note_failure(a, plan, failed_verse)
        if progress:
            self.agent_fails[a] = 0
            self.pacer.done(a, started, rt.committed)
        else:
            if cooled:  # the provider told us when to come back: go to the next one now, no backoff
                return
            self.agent_fails[a] += 1
            if err is not None and kind != "busy":
                self._warn(f"err-{prov.name}-{kind}", "%s call failed (%s): %s", prov.name, kind, (err.message or "")[:120])
            self.pacer.hold(a, self._backoff(self.agent_fails[a], err, soft=err is None))

    def _backoff(self, n: int, err, soft: bool) -> float:
        d = min(300.0, (1.0 if soft else 2.0) * 2 ** min(n - 1, 8)) * random.uniform(0.5, 1.0)
        if err is not None and err.retry_after:
            d = max(d, min(err.retry_after, 600.0))
        return d

    def _actual_cost(self, prov, req, comp, err, rt) -> float:
        if comp is not None and comp.cost_usd is not None:
            return comp.cost_usd
        if err is not None and err.kind in ("auth", "rate_limit", "model_unavailable", "server", "network", "busy"):
            return 0.0
        return prov.cost(est_tokens(req.system) + est_tokens(req.user), rt.chars)

    def _on_text(self, a: int, rt: _RT, text: str) -> bool:
        rt.chars += len(text.strip())
        if rt.finished:
            return not rt.prov.paid  # paid: keep reading so the final usage chunk arrives
        for i in rt.parser.feed(text):
            self._commit(a, rt, i)
        if rt.parser.done:
            rt.finished = True
            return not rt.prov.paid
        return False

    def _commit(self, a: int, rt: _RT, i: int):
        """Verified verse i: ledger first (cursor and counts in one transaction), then memory, then the event."""
        plan = rt.plan
        v = plan.verses[i]
        cur = plan.after(i)
        now, mono = self.clock.wall(), self.clock.mono()
        if self.last_unit_mono is not None:
            silence = mono - self.last_unit_mono
        else:
            lw = self.ledger.last_unit_wall()
            silence = max(0.0, now - lw) if lw else 0.0
        self.ledger.commit_unit(agent=a, station=plan.station, day=self.calendar.today_iso(), cur=cur, now_wall=now,
                                silence_s=silence, max_silence_s=self.cfg.max_silence_min * 60)
        self.directory.apply(cur)
        rt.committed = i + 1
        self.fail_counts.pop(plan.fail_key(i), None)
        self.last_unit_mono = mono
        self.active_until[a] = mono + 12
        self.events.add(a, 1)
        self.voices[plan.station] = (a, v.work_en, "\n".join(v.text.split("\n")[:2]))

    def _note_failure(self, a: int, plan, idx: int):
        day = self.calendar.today_iso()
        try:
            self.ledger.add_correction(day)
        except LedgerBusy:
            pass
        key = plan.fail_key(idx)
        n = self.fail_counts.get(key, 0) + 1
        if n < 3:
            self.fail_counts[key] = n
            return
        self.fail_counts.pop(key, None)
        v = plan.verses[idx]
        try:
            self.ledger.commit_skip(agent=a, station=plan.station, day=day, cur=plan.after(idx), work_id=v.work_id, n=v.n,
                                    text=v.text[:200], now_wall=self.clock.wall())
        except LedgerBusy:
            return
        self.directory.apply(plan.after(idx))
        log.warning("verse stuck, skipped: station %d work %s n=%d (agent %d)", plan.station, v.work_id, v.n, a)

    # ---- background loops ----------------------------------------------
    async def _ticker(self):
        while True:
            await asyncio.sleep(1.0)
            self.events.tick()

    async def _summary_loop(self):
        while True:
            await asyncio.sleep(60)
            log.info("mode=%s provider=%s units=%d today=%d silence=%.0fs relay=%s", self.mode(), self.router.label(),
                     self.ledger.total_units(), self.ledger.day_row(self.calendar.today_iso())[0], self.silence_s(),
                     bool(self.relay and self.relay.connected))

    async def _gathering_loop(self):
        while True:
            await asyncio.sleep(2)
            try:
                if self.phase != "praying" or self.paused:
                    continue
                day = self.calendar.today_iso()
                row = self.ledger.gathering_row(day)
                if row and row[1] is not None:
                    continue  # already done today
                if not row and self.calendar.now_local().time() < self.cfg.gathering_time:
                    continue
                if not self.router.usable():
                    continue  # do not burn the day's gathering while nothing can recite
                await self._run_gathering(day, resumed=row is not None)
            except LedgerBusy:
                pass

    def _set_gathering_sankalpa(self, day: str):
        """Render the day's sankalpa once, at gathering time. Never a half-filled one: on any failure use the simple line."""
        lines = [self.cfg.sankalpa_line()]
        if self.sankalpa.mode == "full":
            try:
                lines, p = self.sankalpa.render(_date.fromisoformat(day))
                log.info("sankalpa for %s: %s %s, %s, %s%s", day, p.plain()["paksha"], p.plain()["tithi"],
                         p.plain()["nakshatra"], p.plain()["masa"], " (near a boundary)" if p.near_boundary else "")
            except Exception:
                log.exception("sankalpa render failed for %s; reciting the simple sankalpa", day)
                lines = [self.cfg.sankalpa_line()]
        self.directory.set_sankalpa(lines)

    async def _run_gathering(self, day: str, resumed: bool):
        if not resumed:
            self.ledger.gathering_start(day, self.clock.wall())
        log.info("gathering begins (%s%s)", day, ", resumed" if resumed else "")
        self._set_gathering_sankalpa(day)
        self.gather_day, self.phase = day, "gathering"
        started, complete = self.clock.mono(), False
        try:
            while True:
                if all(self.directory.gather_done(day, a) for a in range(AGENTS)):
                    complete = True
                    break
                if self.clock.mono() - started > self.cfg.gathering_max_min * 60:
                    log.warning("gathering timed out; ending it incomplete")
                    break
                await asyncio.sleep(1)
        finally:
            self.phase = "praying"
        self.ledger.gathering_finish(day, self.clock.wall(), complete)
        log.info("gathering ends (complete=%s)", complete)

    async def _control_loop(self):
        last_hb = last_file = last_exp = -1e9
        while True:
            now = self.clock.mono()
            try:
                self.paused = self.ledger.control_get("paused") == "1"
                if self.ledger.control_get("stop") == "1":
                    self.request_stop()
                if now - last_hb >= 2:
                    self.ledger.control_set("runtime", json.dumps(self.runtime_status()))
                    last_hb = now
                if now - last_file >= self.cfg.control_poll_s:
                    last_file = now
                    self._poll_control_file()
                if now - last_exp >= self.cfg.status_export_every_s:
                    last_exp = now
                    self._export_status()
                self._watchdog()
            except LedgerBusy:
                pass
            except Exception:
                log.exception("control loop error")
            await asyncio.sleep(1)

    def _poll_control_file(self):
        try:
            data = json.loads(Path(self.cfg.control_path).read_text(encoding="utf-8"))
            cmd, cid = data["command"], data["id"]
            if cmd not in ("pause", "resume") or not isinstance(cid, str) or not cid:
                return
        except Exception:
            return  # missing or malformed: ignored
        if self.ledger.meta_get("control_last_id") == cid:
            return
        self.ledger.control_set("paused", "1" if cmd == "pause" else "0")
        self.ledger.meta_set("control_last_id", cid)
        self.paused = cmd == "pause"
        log.info("control file: %s (id %s)", cmd, cid[:40])

    def _export_status(self):
        path = Path(self.cfg.status_export_path)
        if not path.parent.is_dir():
            if not self._export_warned:
                self._export_warned = True
                log.warning("status export dir %s does not exist; export disabled until it does", path.parent)
            return
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(compose_status(self.ledger, self.cfg, self.clock.wall()), indent=2), encoding="utf-8")
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)

    def _watchdog(self):
        limit = self.cfg.watchdog_min * 60
        if limit > 0 and self.intent() == "praying" and self.silence_s() > limit:
            log.critical("watchdog: mode praying and a provider looks healthy (%s) but no unit verified for %.0f min; "
                         "exiting so Docker restarts me. provider health: %s", self.router.label(), self.silence_s() / 60,
                         self.router.provider_health())
            self.exit_code = EXIT_WATCHDOG
            self.request_stop()
