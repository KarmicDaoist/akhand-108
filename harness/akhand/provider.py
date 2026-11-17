"""Providers: OpenAI-compatible chat completions (SSE with non-streaming fallback) and a fake model."""
from __future__ import annotations

import asyncio
import json
import random
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx
from email.utils import parsedate_to_datetime

from . import USER_AGENT
from .config import Config, chat_url

# error kinds: auth, rate_limit, model_unavailable, server, timeout, malformed, network, truncated
HARD_BLOCK = ("auth", "model_unavailable")


class ProviderError(Exception):
    def __init__(self, kind: str, message: str = "", *, retry_after: float | None = None, status: int | None = None):
        super().__init__(f"{kind}: {message}" if message else kind)
        self.kind, self.message, self.retry_after, self.status = kind, message, retry_after, status


@dataclass
class Request:
    system: str
    user: str
    max_tokens: int
    session: str
    verses: tuple[str, ...] = ()  # only the fake model reads this ("the book open")
    meta: dict = field(default_factory=dict)


@dataclass
class Completion:
    text: str = ""
    finish_reason: str | None = "stop"
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float | None = None  # exact charge when the API reports it (opencode.allowance)
    usage_estimated: bool = True
    allowance: Any = None


OnText = Callable[[str], bool]  # return True to stop reading


def est_tokens(s: str) -> int:
    return len(s)  # deliberately pessimistic: about one token per character


def _msg_of(obj: Any) -> str:
    if isinstance(obj, dict):
        e = obj.get("error", obj)
        if isinstance(e, dict):
            return str(e.get("message") or e.get("type") or e.get("code") or "")[:300]
        return str(e)[:300]
    return str(obj)[:300]


MAX_RETRY_AFTER_S = 26 * 3600.0


def parse_retry_after(value: str | None, now: float | None = None) -> float | None:
    """Retry-After as seconds from now: delta-seconds or an HTTP date. Capped at 26 hours."""
    if not value:
        return None
    value = value.strip()
    try:
        secs = float(value)
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
            secs = when.timestamp() - (time.time() if now is None else now)
        except (TypeError, ValueError, IndexError):
            return None
    if secs != secs:  # NaN
        return None
    return max(0.0, min(MAX_RETRY_AFTER_S, secs))


def classify_http(status: int, body: str, retry_after: str | None = None) -> ProviderError:
    """Status code first, body text only to split ambiguous 4xx."""
    msg = body
    try:
        msg = _msg_of(json.loads(body))
    except Exception:
        msg = body[:300]
    low = msg.lower()
    ra = parse_retry_after(retry_after)
    if status in (401, 403):
        return ProviderError("auth", msg, status=status)
    if status == 402:
        return ProviderError("auth", "payment or quota: " + msg, status=status)
    if status == 429:
        return ProviderError("rate_limit", msg, retry_after=ra, status=status)
    if status in (404, 410) or (status in (400, 422) and "model" in low and any(
            w in low for w in ("not found", "does not exist", "unavailable", "deprecated", "removed", "unknown", "not supported"))):
        return ProviderError("model_unavailable", msg, status=status)
    if status in (408, 504):
        return ProviderError("timeout", msg, status=status)
    if status >= 500:
        return ProviderError("server", msg, retry_after=ra, status=status)
    return ProviderError("malformed", msg, status=status)


def classify_error_obj(obj: Any) -> ProviderError:
    """An error object that arrives inside a 200 stream."""
    msg = _msg_of(obj)
    low = msg.lower()
    code = None
    if isinstance(obj, dict) and isinstance(obj.get("error"), dict):
        c = obj["error"].get("code")
        code = c if isinstance(c, int) else None
    if code:
        return classify_http(code, json.dumps(obj))
    if "rate" in low or "too many" in low or "quota" in low:
        return ProviderError("rate_limit", msg)
    if "auth" in low or "api key" in low or "unauthor" in low:
        return ProviderError("auth", msg)
    if "model" in low:
        return ProviderError("model_unavailable", msg)
    return ProviderError("server", msg)


class OpenAICompatProvider:
    paid_default = False

    def __init__(self, name: str, base_url: str, model: str, api_key: str, *, stream: bool = True,
                 reasoning_effort: str = "", price_in: float = 0.0, price_out: float = 0.0,
                 reserve_usd: float = 0.0, timeout_s: float = 120.0, transport: httpx.AsyncBaseTransport | None = None):
        self.name, self.url, self.model, self.api_key = name, chat_url(base_url), model, api_key
        self.stream_enabled = stream
        self.reasoning_effort = reasoning_effort
        self.price_in, self.price_out, self.reserve_usd = price_in, price_out, reserve_usd
        self.total_timeout = timeout_s
        self.paid = price_in > 0 or price_out > 0
        self.last_allowance: Any = None
        self._no_stream_until = 0.0
        self._effort_rejected = False
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=60.0, write=15.0, pool=15.0),
            limits=httpx.Limits(max_connections=64, max_keepalive_connections=32),
            transport=transport, headers={"User-Agent": USER_AGENT})

    def cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        return prompt_tokens * self.price_in / 1e6 + completion_tokens * self.price_out / 1e6

    async def aclose(self):
        await self.client.aclose()

    # -------------------------------------------------------------
    def _body(self, req: Request, stream: bool, with_effort: bool) -> dict:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": req.system}, {"role": "user", "content": req.user}],
            "max_tokens": req.max_tokens,
            "temperature": 0,
            "stream": stream,
        }
        if stream:
            body["stream_options"] = {"include_usage": True}
        if with_effort and self.reasoning_effort and not self._effort_rejected:
            body["reasoning_effort"] = self.reasoning_effort
        return body

    def _headers(self, req: Request) -> dict:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json",
                "x-opencode-session": req.session, "User-Agent": USER_AGENT}

    async def complete(self, req: Request, on_text: OnText) -> Completion:
        try:
            async with asyncio.timeout(self.total_timeout):
                return await self._complete(req, on_text)
        except TimeoutError:
            raise ProviderError("timeout", f"no complete reply within {self.total_timeout:.0f}s")
        except httpx.TimeoutException as e:
            raise ProviderError("timeout", type(e).__name__)
        except httpx.RemoteProtocolError as e:
            raise ProviderError("truncated", str(e)[:200])
        except httpx.TransportError as e:
            raise ProviderError("network", f"{type(e).__name__}: {str(e)[:150]}")

    async def _complete(self, req, on_text):
        stream = self.stream_enabled and time.monotonic() >= self._no_stream_until
        for attempt in range(3):  # retries are about request shape only, never about content
            try:
                if stream:
                    return await self._stream(req, on_text)
                return await self._plain(req, on_text)
            except _Retry as r:
                if r.drop_effort:
                    self._effort_rejected = True
                if r.drop_stream:
                    self._no_stream_until = time.monotonic() + 600
                    stream = False
        raise ProviderError("malformed", "request shape rejected repeatedly")

    def _check_status(self, status: int, text: str, retry_after: str | None, streaming: bool):
        low = text.lower()
        if status == 400 and "reasoning_effort" in low and not self._effort_rejected:
            raise _Retry(drop_effort=True)
        if status in (400, 422) and streaming and ("stream" in low):
            raise _Retry(drop_stream=True)
        raise classify_http(status, text, retry_after)

    async def _plain(self, req, on_text) -> Completion:
        r = await self.client.post(self.url, json=self._body(req, False, True), headers=self._headers(req))
        if r.status_code != 200:
            self._check_status(r.status_code, r.text[:2000], r.headers.get("retry-after"), False)
        return self._from_json(r.content, req, on_text)

    def _from_json(self, raw: bytes, req, on_text) -> Completion:
        try:
            obj = json.loads(raw)
        except Exception:
            raise ProviderError("malformed", "reply is not JSON")
        if isinstance(obj, dict) and obj.get("error") and not obj.get("choices"):
            raise classify_error_obj(obj)
        try:
            ch = obj["choices"][0]
            content = ch.get("message", {}).get("content") or ""
            fin = ch.get("finish_reason") or "stop"
        except Exception:
            raise ProviderError("malformed", "no choices in reply")
        if isinstance(content, list):
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        comp = Completion(text=content, finish_reason=fin)
        self._apply_usage(comp, obj.get("usage"), req)
        if content:
            on_text(content)
        return comp

    async def _stream(self, req, on_text) -> Completion:
        comp = Completion(text="", finish_reason=None)
        usage = None
        saw_chunk = saw_done = False
        async with self.client.stream("POST", self.url, json=self._body(req, True, True), headers=self._headers(req)) as r:
            if r.status_code != 200:
                text = (await r.aread())[:2000].decode("utf-8", "replace")
                self._check_status(r.status_code, text, r.headers.get("retry-after"), True)
            if "text/event-stream" not in r.headers.get("content-type", ""):
                raw = await r.aread()  # server ignored stream=true and sent plain JSON
                return self._from_json(raw, req, on_text)
            stopped = False
            async for line in r.aiter_lines():
                line = line.strip()
                if not line or line.startswith(":") or not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    saw_done = True
                    break
                try:
                    obj = json.loads(payload)
                except Exception:
                    continue
                if not isinstance(obj, dict):
                    continue
                saw_chunk = True
                if obj.get("error") and not obj.get("choices"):
                    raise classify_error_obj(obj)
                if obj.get("usage"):
                    usage = obj["usage"]
                for ch in obj.get("choices") or []:
                    delta = ch.get("delta") or {}
                    piece = delta.get("content")
                    if isinstance(piece, list):
                        piece = "".join(p.get("text", "") for p in piece if isinstance(p, dict))
                    if piece:
                        comp.text += piece
                        if not stopped and on_text(piece):
                            stopped = True
                            comp.finish_reason = "aborted"
                    if ch.get("finish_reason"):
                        if comp.finish_reason != "aborted":
                            comp.finish_reason = ch["finish_reason"]
                        saw_done = saw_done or False
                if stopped and not self.paid:
                    break  # free provider: stop paying attention (and close the connection)
        if not saw_chunk:
            raise ProviderError("malformed", "stream carried no data")
        if not saw_done and comp.finish_reason is None and usage is None:
            raise ProviderError("truncated", "stream ended without a finish marker")
        if comp.finish_reason is None:
            comp.finish_reason = "stop"
        self._apply_usage(comp, usage, req)
        return comp

    def _apply_usage(self, comp: Completion, usage: Any, req: Request):
        pt = ct = None
        if isinstance(usage, dict):
            pt, ct = usage.get("prompt_tokens"), usage.get("completion_tokens")
            oc = usage.get("opencode")
            if isinstance(oc, dict) and oc.get("allowance") is not None:
                comp.allowance = oc["allowance"]
                self.last_allowance = oc["allowance"]
                try:
                    comp.cost_usd = float(oc["allowance"])
                except (TypeError, ValueError):
                    pass
        if isinstance(pt, int) and isinstance(ct, int):
            comp.prompt_tokens, comp.completion_tokens, comp.usage_estimated = pt, ct, False
        else:
            comp.prompt_tokens = est_tokens(req.system) + est_tokens(req.user)
            comp.completion_tokens = est_tokens(comp.text)
            comp.usage_estimated = True
        if comp.cost_usd is None:
            comp.cost_usd = self.cost(comp.prompt_tokens, comp.completion_tokens)


class _Retry(Exception):
    def __init__(self, drop_effort=False, drop_stream=False):
        self.drop_effort, self.drop_stream = drop_effort, drop_stream


# ---------------------------------------------------------------------------
class FakeProvider:
    """Offline model that recites with the book open, with configurable misbehaviour."""

    ERROR_KINDS = ("server", "timeout", "rate_limit", "truncated", "empty")

    def __init__(self, name: str, *, error_rate=0.0, wrong_rate=0.0, latency=0.2, commentary_rate=0.15,
                 price_in=0.0, price_out=0.0, reserve_usd=0.0, seed=None, quota_after=None, quota_retry_after_s=3600.0):
        self.quota_after, self.quota_retry_after_s = quota_after, quota_retry_after_s
        self.quota_reset_at = 0.0  # wall time at which the exhausted fake allowance comes back
        self.name, self.error_rate, self.wrong_rate = name, error_rate, wrong_rate
        self.latency, self.commentary_rate = latency, commentary_rate
        self.price_in, self.price_out, self.reserve_usd = price_in, price_out, reserve_usd
        self.paid = price_in > 0 or price_out > 0
        self.rng = random.Random(seed)
        self.script: deque[str] = deque()  # kinds consumed one per call ("ok" for a good call)
        self.always: str | None = None
        self.calls = 0
        self.last_allowance = None
        self.inflight_by_station: Counter = Counter()
        self.max_inflight_by_station: Counter = Counter()

    def cost(self, pt, ct):
        return pt * self.price_in / 1e6 + ct * self.price_out / 1e6

    async def aclose(self):
        pass

    def _pick(self) -> str:
        if self.script:
            return self.script.popleft()
        if self.always:
            return self.always
        if self.error_rate and self.rng.random() < self.error_rate:
            return self.rng.choice(self.ERROR_KINDS)
        return "ok"

    def _mutate(self, v: str) -> str:
        words = v.split()
        k = self.rng.randrange(len(words)) if words else 0
        if words:
            words[k] = "अशुद्ध"
        return " ".join(words)

    def _render(self, verses) -> str:
        parts = []
        for i, v in enumerate(verses):
            if self.wrong_rate and self.rng.random() < self.wrong_rate:
                v = self._mutate(v)
            if self.commentary_rate and self.rng.random() < self.commentary_rate:
                v = f"{i + 1}. {v}"
            parts.append(v)
        text = "\n\n".join(parts)
        if self.commentary_rate and self.rng.random() < self.commentary_rate:
            text = "Here is the recitation:\n\n" + text
        if self.commentary_rate and self.rng.random() < self.commentary_rate:
            text += "\n\nLet me know if you need more."
        return text

    async def complete(self, req: Request, on_text: OnText) -> Completion:
        self.calls += 1
        if self.quota_after is not None and (self.calls > self.quota_after or time.time() < self.quota_reset_at):
            if time.time() >= self.quota_reset_at:
                self.quota_reset_at = time.time() + self.quota_retry_after_s
            await asyncio.sleep(0.01)
            raise ProviderError("rate_limit", "fake daily allowance used", retry_after=self.quota_reset_at - time.time())
        st = req.meta.get("station")
        self.inflight_by_station[st] += 1
        self.max_inflight_by_station[st] = max(self.max_inflight_by_station[st], self.inflight_by_station[st])
        try:
            kind = self._pick()
            await asyncio.sleep(self.latency * 0.3 * self.rng.random() if self.latency else 0)
            if kind in ("auth", "model_unavailable", "server", "timeout", "rate_limit", "malformed", "network"):
                raise ProviderError(kind, "fake", retry_after=1.0 if kind == "rate_limit" else None)
            if kind == "empty":
                return Completion(text="", finish_reason="stop", prompt_tokens=est_tokens(req.user) // 2, completion_tokens=0,
                                  cost_usd=0.0, usage_estimated=False)
            text = self._render(req.verses)
            if kind == "truncated":
                text = text[: max(1, len(text) // 2)]
            chunks, i = [], 0
            while i < len(text):
                n = self.rng.randint(3, 14)
                chunks.append(text[i:i + n])
                i += n
            sent, stopped = "", False
            for c in chunks:
                if self.latency:
                    await asyncio.sleep(self.latency * 0.7 / max(1, len(chunks)))
                else:
                    await asyncio.sleep(0)
                sent += c
                if not stopped and on_text(c):
                    stopped = True
                    if not self.paid:
                        break
            pt = (len(req.system) + len(req.user)) // 2
            ct = len(sent) // 2
            if kind == "truncated":
                raise ProviderError("truncated", "fake cut-off")
            comp = Completion(text=sent, finish_reason="aborted" if stopped else "stop", prompt_tokens=pt,
                              completion_tokens=ct, usage_estimated=False)
            comp.cost_usd = self.cost(pt, ct)
            return comp
        finally:
            self.inflight_by_station[st] -= 1


def _fake_quota(spec: str) -> dict[str, int]:
    out = {}
    for part in spec.split(","):
        if ":" in part:
            k, v = part.rsplit(":", 1)
            out[k.strip()] = int(v)
    return out


def build_providers(cfg: Config):
    """Returns (free providers in order, paid backup)."""
    if cfg.provider == "fake":
        quota = _fake_quota(cfg.fake_quota)
        free = [FakeProvider(m.name, error_rate=cfg.fake_error_rate, wrong_rate=cfg.fake_wrong_rate, latency=cfg.fake_latency,
                             quota_after=quota.get(m.name), quota_retry_after_s=cfg.fake_quota_retry_after_s) for m in cfg.free]
        return free, FakeProvider("muse", error_rate=cfg.fake_error_rate, wrong_rate=cfg.fake_wrong_rate, latency=cfg.fake_latency,
                                  price_in=cfg.backup_price_in, price_out=cfg.backup_price_out, reserve_usd=cfg.backup_reserve_usd)
    if cfg.provider != "opencode":
        raise SystemExit(f"config: PROVIDER={cfg.provider!r} must be opencode or fake")
    free = [OpenAICompatProvider(m.name, m.base_url, m.model, m.api_key, stream=m.stream, reasoning_effort=m.reasoning,
                                 timeout_s=m.timeout_s) for m in cfg.free]
    backup = OpenAICompatProvider("muse", cfg.backup_base_url, cfg.backup_model, cfg.backup_api_key,
                                  stream=cfg.backup_stream, reasoning_effort=cfg.backup_reasoning,
                                  price_in=cfg.backup_price_in, price_out=cfg.backup_price_out,
                                  reserve_usd=cfg.backup_reserve_usd, timeout_s=cfg.request_timeout_s)
    return free, backup
