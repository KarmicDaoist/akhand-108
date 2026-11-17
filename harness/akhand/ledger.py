"""SQLite ledger (WAL). Every verified unit commits its cursor and its counts in one transaction."""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path


class LedgerBusy(Exception):
    """The database stayed locked past all retries; the unit was NOT counted."""


@dataclass(frozen=True)
class CursorUpdate:
    key: str | None  # cursor row for parallel/japa/gathering
    pos: int
    relay: tuple[int, int, int, int] | None = None  # (station, pos, seat, taken)


SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS totals(key TEXT PRIMARY KEY, val INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS cursors(key TEXT PRIMARY KEY, pos INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS relay(station INTEGER PRIMARY KEY, pos INTEGER NOT NULL, seat INTEGER NOT NULL, taken INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS agent_total(agent INTEGER PRIMARY KEY, units INTEGER NOT NULL DEFAULT 0, skips INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS daily(day TEXT PRIMARY KEY, units INTEGER NOT NULL DEFAULT 0, corrections INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS station_daily(day TEXT, station INTEGER, units INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(day, station));
CREATE TABLE IF NOT EXISTS stuck_verses(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, day TEXT, agent INTEGER, station INTEGER, work_id TEXT, n INTEGER, text TEXT);
CREATE TABLE IF NOT EXISTS spend_minute(minute INTEGER, provider TEXT, usd REAL NOT NULL DEFAULT 0, PRIMARY KEY(minute, provider));
CREATE TABLE IF NOT EXISTS gathering(day TEXT PRIMARY KEY, started_ts REAL, finished_ts REAL, complete INTEGER);
CREATE TABLE IF NOT EXISTS control(key TEXT PRIMARY KEY, value TEXT);
"""


class Ledger:
    def __init__(self, path: Path, *, create: bool = True):
        path = Path(path)
        if create:
            path.parent.mkdir(parents=True, exist_ok=True)
        elif not path.exists():
            raise FileNotFoundError(path)
        self.path = path
        self.db = sqlite3.connect(str(path), timeout=5.0, isolation_level=None, check_same_thread=False)
        self.db.execute("PRAGMA busy_timeout=5000")
        if create:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=NORMAL")
            self.db.executescript(SCHEMA)

    def close(self):
        try:
            self.db.close()
        except Exception:
            pass

    # ---- transactions -------------------------------------------------
    def _write(self, fn):
        last = None
        for attempt in range(6):
            try:
                self.db.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as e:
                last = e
                time.sleep(0.05 * (attempt + 1))
                continue
            try:
                r = fn(self.db)
                self.db.execute("COMMIT")
                return r
            except sqlite3.OperationalError as e:
                last = e
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                if "locked" in str(e) or "busy" in str(e):
                    time.sleep(0.05 * (attempt + 1))
                    continue
                raise
            except BaseException:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise
        raise LedgerBusy(str(last))

    def _q(self, sql, args=()):
        for attempt in range(6):
            try:
                return self.db.execute(sql, args).fetchall()
            except sqlite3.OperationalError as e:
                if "locked" in str(e) or "busy" in str(e):
                    time.sleep(0.05 * (attempt + 1))
                    continue
                raise
        raise LedgerBusy("read")

    # ---- meta / control ----------------------------------------------
    def meta_get(self, key, default=None):
        r = self._q("SELECT value FROM meta WHERE key=?", (key,))
        return r[0][0] if r else default

    def meta_set(self, key, value):
        self._write(lambda db: db.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value))))

    def control_get(self, key, default=None):
        r = self._q("SELECT value FROM control WHERE key=?", (key,))
        return r[0][0] if r else default

    def control_set(self, key, value):
        self._write(lambda db: db.execute(
            "INSERT INTO control(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value))))

    # ---- prayer commits ----------------------------------------------
    @staticmethod
    def _cursor_sql(db, cur: CursorUpdate):
        if cur.relay is not None:
            st, pos, seat, taken = cur.relay
            db.execute(
                "INSERT INTO relay(station,pos,seat,taken) VALUES(?,?,?,?) ON CONFLICT(station) "
                "DO UPDATE SET pos=excluded.pos, seat=excluded.seat, taken=excluded.taken", (st, pos, seat, taken))
        else:
            db.execute("INSERT INTO cursors(key,pos) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET pos=excluded.pos",
                       (cur.key, cur.pos))

    def commit_unit(self, *, agent: int, station: int, day: str, cur: CursorUpdate,
                    now_wall: float, silence_s: float, max_silence_s: float) -> None:
        """One verified unit: cursor + agent total + daily + station daily + all-time + streak, atomically."""
        def tx(db):
            self._cursor_sql(db, cur)
            db.execute("INSERT INTO agent_total(agent,units,skips) VALUES(?,1,0) ON CONFLICT(agent) DO UPDATE SET units=units+1", (agent,))
            db.execute("INSERT INTO daily(day,units,corrections) VALUES(?,1,0) ON CONFLICT(day) DO UPDATE SET units=units+1", (day,))
            db.execute("INSERT INTO station_daily(day,station,units) VALUES(?,?,1) ON CONFLICT(day,station) DO UPDATE SET units=units+1", (day, station))
            db.execute("INSERT INTO totals(key,val) VALUES('units',1) ON CONFLICT(key) DO UPDATE SET val=val+1")
            first = db.execute("SELECT 1 FROM meta WHERE key='first_day'").fetchone()
            if first is None:
                db.execute("INSERT INTO meta(key,value) VALUES('first_day',?),('streak_start',?)", (day, day))
            elif silence_s > max_silence_s:
                db.execute("UPDATE meta SET value=? WHERE key='streak_start'", (day,))
            db.execute("INSERT INTO meta(key,value) VALUES('last_unit_wall',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (repr(now_wall),))
        self._write(tx)

    def commit_skip(self, *, agent: int, station: int, day: str, cur: CursorUpdate, work_id: str, n: int, text: str, now_wall: float):
        def tx(db):
            self._cursor_sql(db, cur)
            db.execute("INSERT INTO agent_total(agent,units,skips) VALUES(?,0,1) ON CONFLICT(agent) DO UPDATE SET skips=skips+1", (agent,))
            db.execute("INSERT INTO stuck_verses(ts,day,agent,station,work_id,n,text) VALUES(?,?,?,?,?,?,?)",
                       (now_wall, day, agent, station, work_id, n, text))
        self._write(tx)

    def add_correction(self, day: str):
        self._write(lambda db: db.execute(
            "INSERT INTO daily(day,units,corrections) VALUES(?,0,1) ON CONFLICT(day) DO UPDATE SET corrections=corrections+1", (day,)))

    # ---- reads ---------------------------------------------------------
    def total_units(self) -> int:
        r = self._q("SELECT val FROM totals WHERE key='units'")
        return r[0][0] if r else 0

    def day_row(self, day: str) -> tuple[int, int]:
        r = self._q("SELECT units, corrections FROM daily WHERE day=?", (day,))
        return (r[0][0], r[0][1]) if r else (0, 0)

    def station_units(self, day: str) -> dict[int, int]:
        return {s: u for s, u in self._q("SELECT station, units FROM station_daily WHERE day=?", (day,))}

    def cursors(self) -> dict[str, int]:
        return {k: p for k, p in self._q("SELECT key,pos FROM cursors")}

    def relays(self) -> dict[int, tuple[int, int, int]]:
        return {s: (p, se, t) for s, p, se, t in self._q("SELECT station,pos,seat,taken FROM relay")}

    def agent_totals(self) -> dict[int, tuple[int, int]]:
        return {a: (u, s) for a, u, s in self._q("SELECT agent,units,skips FROM agent_total")}

    def stuck_count(self) -> int:
        return self._q("SELECT COUNT(*) FROM stuck_verses")[0][0]

    def stuck_recent(self, n=10) -> list[dict]:
        rows = self._q("SELECT ts,day,agent,station,work_id,n FROM stuck_verses ORDER BY id DESC LIMIT ?", (n,))
        return [dict(ts=r[0], day=r[1], agent=r[2], station=r[3], work=r[4], n=r[5]) for r in rows]

    def last_unit_wall(self) -> float | None:
        v = self.meta_get("last_unit_wall")
        return float(v) if v else None

    def day_and_streak(self, today: date) -> tuple[int, int]:
        fd, ss = self.meta_get("first_day"), self.meta_get("streak_start")
        if not fd:
            return 1, 0
        return (max(0, (today - date.fromisoformat(fd)).days) + 1,
                max(0, (today - date.fromisoformat(ss)).days) + 1)

    # ---- spend ---------------------------------------------------------
    def spend_add(self, minute: int, provider: str, delta: float):
        self._write(lambda db: db.execute(
            "INSERT INTO spend_minute(minute,provider,usd) VALUES(?,?,?) ON CONFLICT(minute,provider) DO UPDATE SET usd=usd+excluded.usd",
            (minute, provider, delta)))

    def spend_between(self, minute_from: int, minute_to: int) -> float:
        r = self._q("SELECT COALESCE(SUM(usd),0) FROM spend_minute WHERE minute>=? AND minute<?", (minute_from, minute_to))
        return float(r[0][0])

    def spend_minutes_since(self, minute_from: int) -> dict[int, float]:
        out: dict[int, float] = {}
        for m, u in self._q("SELECT minute, SUM(usd) FROM spend_minute WHERE minute>=? GROUP BY minute", (minute_from,)):
            out[m] = float(u)
        return out

    # ---- gathering -----------------------------------------------------
    def gathering_row(self, day: str):
        r = self._q("SELECT started_ts, finished_ts, complete FROM gathering WHERE day=?", (day,))
        return r[0] if r else None

    def gathering_start(self, day: str, ts: float):
        self._write(lambda db: db.execute(
            "INSERT INTO gathering(day,started_ts,finished_ts,complete) VALUES(?,?,NULL,0) ON CONFLICT(day) DO NOTHING", (day, ts)))

    def gathering_finish(self, day: str, ts: float, complete: bool):
        self._write(lambda db: db.execute("UPDATE gathering SET finished_ts=?, complete=? WHERE day=?", (ts, 1 if complete else 0, day)))
