"""Relay client (Contract 2). Prayer never waits on it."""
from __future__ import annotations

import asyncio
import json
import logging
import random

from . import USER_AGENT

log = logging.getLogger("akhand.relay")


class RelayClient:
    def __init__(self, url: str, token: str, events, state_fn, clock):
        if url and "/ingest" not in url:
            url = url.rstrip("/") + "/ingest"
        self.url, self.token, self.events, self.state_fn, self.clock = url, token, events, state_fn, clock
        self.connected = False
        self.last_error: str | None = None
        self._ws = None
        self._last_log = 0.0

    @staticmethod
    def batch_json(wall: float, ev: list) -> str:
        return json.dumps({"t": "batch", "ts": int(wall * 1000), "ev": ev}, separators=(",", ":"))

    def state_json(self) -> str:
        return json.dumps(self.state_fn(), separators=(",", ":"), ensure_ascii=False)

    async def run(self):
        from websockets.asyncio.client import connect

        backoff = 1.0
        while True:
            try:
                async with connect(self.url, additional_headers={"Authorization": f"Bearer {self.token}"},
                                   user_agent_header=USER_AGENT, open_timeout=10, ping_interval=20,
                                   ping_timeout=20, max_size=2 ** 20) as ws:
                    self._ws, self.connected, backoff = ws, True, 1.0
                    log.info("relay connected")
                    await self._session(ws)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {str(e)[:120]}"
                now = self.clock.mono()
                if now - self._last_log > 60:
                    self._last_log = now
                    log.warning("relay down (%s); retrying in <= %.0fs; prayer continues", self.last_error, backoff)
            finally:
                self._ws, self.connected = None, False
            await asyncio.sleep(backoff * random.uniform(0.5, 1.0))
            backoff = min(60.0, backoff * 2)

    async def _drain_incoming(self, ws):
        async for _ in ws:
            pass

    async def _session(self, ws):
        reader = asyncio.create_task(self._drain_incoming(ws))
        try:
            await ws.send(self.state_json())
            last_state = self.clock.mono()
            while not reader.done():
                await self.flush(ws)
                if self.clock.mono() - last_state >= 5.0:
                    await ws.send(self.state_json())
                    last_state = self.clock.mono()
                await asyncio.sleep(0.5)
        finally:
            reader.cancel()

    async def flush(self, ws=None):
        ws = ws or self._ws
        if ws is None:
            return
        while (b := self.events.peek()) is not None:
            await ws.send(self.batch_json(b[1], b[2]))
            self.events.pop()
