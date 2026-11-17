"""Environment-driven configuration."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import time as dtime, timedelta, timezone, tzinfo
from pathlib import Path

from .sankalpa import SankalpaSettings

AGENTS = 108
SEATS = 9
STATIONS = 12
DEFAULT_BASE_URL = "https://opencode.ai/zen/go/v1"
DEFAULT_SANKALPA = "ॐ श्री गणेशाय नमः। {name} के कल्याण के लिए यह अखंड पाठ संकल्पित है।"


def chat_url(base: str) -> str:
    base = base.rstrip("/")
    return base if base.endswith("/chat/completions") else base + "/chat/completions"


def _s(env, key, default=""):
    v = env.get(key)
    return default if v is None or v.strip() == "" else v.strip()


def _num(env, key, default, cast):
    v = env.get(key)
    if v is None or v.strip() == "":
        return default
    try:
        return cast(v.strip())
    except ValueError:
        raise SystemExit(f"config: {key}={v!r} is not a valid {cast.__name__}")


def short_name(model: str) -> str:
    """iceman-contributor-free -> iceman, beam-free -> beam, longcat-2.5-free -> longcat."""
    s = model.strip()
    for suf in ("-free", "-contributor"):
        if s.endswith(suf):
            s = s[: -len(suf)]
    return re.sub(r"-\d.*$", "", s) or model.strip()


DEFAULT_FREE_MODELS = "iceman-contributor-free,beam-free,longcat-2.5-free"
DEFAULT_EFFORT = {"iceman": "low"}  # other free models send no reasoning_effort unless configured
DEFAULT_TIMEOUT_S = {"longcat": 180.0}


@dataclass(frozen=True)
class FreeModel:
    model: str
    name: str  # short unique name, e.g. "beam"
    base_url: str
    api_key: str
    reasoning: str
    stream: bool
    timeout_s: float
    verses_per_call: int


def _free_key(env, name: str, idx: int, field: str, legacy: str | None):
    """FREE_<NAME>_<FIELD> beats FREE_<N>_<FIELD> (N counts from 1) beats the legacy PRIMARY_* name (first model only)."""
    up = re.sub(r"[^A-Z0-9]+", "_", name.upper())
    cands = [f"FREE_{up}_{field}", f"FREE_{idx}_{field}"] + ([legacy] if idx == 1 and legacy else [])
    for k in cands:
        v = env.get(k)
        if v is not None and v.strip() != "":
            return k
    return None


def _free_models(env, key: str, base: str, verses_per_call: int, request_timeout_s: float) -> tuple[FreeModel, ...]:
    listed = _s(env, "FREE_MODELS")
    if listed:
        models = [m.strip() for m in listed.split(",") if m.strip()]
    else:
        models = DEFAULT_FREE_MODELS.split(",")
        if _s(env, "PRIMARY_MODEL"):
            models[0] = _s(env, "PRIMARY_MODEL")  # deployed .env: PRIMARY_MODEL is the first free model
    if not models:
        raise SystemExit("config: FREE_MODELS is empty")
    out, used = [], {"muse", "none"}
    for i, model in enumerate(models, 1):
        name = short_name(model)
        if name in used:
            name = f"{name}{i}"
        used.add(name)

        def get(field, legacy, default, cast=str):
            k = _free_key(env, name, i, field, legacy)
            if k is None:
                return default
            return env[k].strip() if cast is str else _num(env, k, default, cast)

        effort = get("REASONING_EFFORT", "PRIMARY_REASONING_EFFORT", DEFAULT_EFFORT.get(name, ""))
        k = _free_key(env, name, i, "STREAM", "PRIMARY_STREAM")
        out.append(FreeModel(
            model=model, name=name,
            base_url=get("BASE_URL", "PRIMARY_BASE_URL", base),
            api_key=get("API_KEY", "PRIMARY_API_KEY", key),
            reasoning="" if effort.lower() in ("none", "off") else effort,
            stream=_bool(env, k, True) if k else True,
            timeout_s=get("TIMEOUT_S", None, DEFAULT_TIMEOUT_S.get(name, request_timeout_s), float),
            verses_per_call=get("VERSES_PER_CALL", None, verses_per_call, int)))
    return tuple(out)


def _bool(env, key, default):
    v = env.get(key)
    if v is None or v.strip() == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Config:
    data_dir: Path
    library_path: Path
    provider: str  # "opencode" | "fake"
    free: tuple  # tuple[FreeModel, ...], tried in order
    backup_base_url: str
    backup_model: str
    backup_api_key: str
    backup_stream: bool
    backup_reasoning: str
    backup_price_in: float  # USD per 1M tokens
    backup_price_out: float
    backup_reserve_usd: float
    backup_paced: bool
    backup_verses_per_call: int
    sankalpa_name: str
    sankalpa_text: str
    gathering_time: dtime
    tz: str
    verses_per_hour: float
    max_concurrency: int
    verses_per_call: int
    relay_turn_verses: int
    japa_reps: int
    month_usd: float
    week_usd: float
    max_silence_min: float
    relay_url: str
    relay_token: str
    fake_error_rate: float
    fake_wrong_rate: float
    fake_latency: float
    fake_quota: str  # "iceman:40,beam:40": that fake free model answers 429 after N calls
    fake_quota_retry_after_s: float
    breaker_window_s: float
    breaker_min_calls: int
    probe_interval_s: float
    auth_rest_s: float
    request_timeout_s: float
    resting_after_s: float
    gathering_max_min: float
    watchdog_min: float
    status_export_path: str
    status_export_every_s: float
    control_path: str
    control_poll_s: float
    sankalpa: SankalpaSettings = SankalpaSettings()  # SANKALPA_MODE=full and friends

    @property
    def tzinfo(self) -> tzinfo:
        try:
            from zoneinfo import ZoneInfo

            return ZoneInfo(self.tz)
        except Exception:
            if self.tz == "Asia/Kolkata":
                return timezone(timedelta(hours=5, minutes=30))
            return timezone.utc

    def sankalpa_line(self) -> str:
        return self.sankalpa_text.replace("{name}", self.sankalpa_name)

    @classmethod
    def from_env(cls, env=None) -> "Config":
        env = os.environ if env is None else env
        key = _s(env, "OPENCODE_GO_API_KEY") or _s(env, "OPENCODE_API_KEY")
        gt = _s(env, "GATHERING_TIME", "06:00")
        try:
            hh, mm = gt.split(":")[:2]
            gtime = dtime(int(hh), int(mm))
        except Exception:
            raise SystemExit(f"config: GATHERING_TIME={gt!r} must be HH:MM")
        base = _s(env, "OPENCODE_BASE_URL", DEFAULT_BASE_URL)
        vpc = _num(env, "VERSES_PER_CALL", 24, int)
        req_timeout = _num(env, "REQUEST_TIMEOUT_S", 120.0, float)
        return cls(
            data_dir=Path(_s(env, "DATA_DIR", "/data")),
            library_path=Path(_s(env, "LIBRARY_PATH", "/app/library/library.json")),
            provider=_s(env, "PROVIDER", "opencode").lower(),
            free=_free_models(env, key, base, vpc, req_timeout),
            backup_base_url=_s(env, "BACKUP_BASE_URL", base),
            backup_model=_s(env, "BACKUP_MODEL", "muse-spark-1.3-contributor"),
            backup_api_key=_s(env, "BACKUP_API_KEY", key),
            backup_stream=_bool(env, "BACKUP_STREAM", True),
            backup_reasoning=_s(env, "BACKUP_REASONING_EFFORT", "minimal"),
            backup_price_in=_num(env, "BACKUP_PRICE_IN_PER_M", 0.10, float),
            backup_price_out=_num(env, "BACKUP_PRICE_OUT_PER_M", 0.20, float),
            backup_reserve_usd=_num(env, "BACKUP_RESERVE_USD", 0.0003, float),
            backup_paced=_bool(env, "BACKUP_PACED", True),
            backup_verses_per_call=_num(env, "BACKUP_VERSES_PER_CALL", vpc, int),
            sankalpa_name=_s(env, "SANKALPA_NAME", "Mehul"),
            sankalpa_text=_s(env, "SANKALPA_TEXT", DEFAULT_SANKALPA),
            gathering_time=gtime,
            tz=_s(env, "TZ", "Asia/Kolkata"),
            verses_per_hour=_num(env, "TARGET_VERSES_PER_AGENT_HOUR", 360.0, float),
            max_concurrency=_num(env, "MAX_CONCURRENCY", 24, int),
            verses_per_call=vpc,
            relay_turn_verses=_num(env, "RELAY_TURN_VERSES", 8, int),
            japa_reps=_num(env, "JAPA_REPS", 27, int),
            month_usd=_num(env, "MUSE_MONTH_USD", 30.0, float),
            week_usd=_num(env, "MUSE_WEEK_USD", 7.0, float),
            max_silence_min=_num(env, "MAX_SILENCE_MIN", 30.0, float),
            relay_url=_s(env, "RELAY_URL"),
            relay_token=_s(env, "RELAY_TOKEN"),
            fake_error_rate=_num(env, "FAKE_ERROR_RATE", 0.0, float),
            fake_wrong_rate=_num(env, "FAKE_WRONG_RATE", 0.0, float),
            fake_latency=_num(env, "FAKE_LATENCY", 0.3, float),
            fake_quota=_s(env, "FAKE_QUOTA"),
            fake_quota_retry_after_s=_num(env, "FAKE_QUOTA_RETRY_AFTER_S", 3600.0, float),
            breaker_window_s=_num(env, "BREAKER_WINDOW_S", 60.0, float),
            breaker_min_calls=_num(env, "BREAKER_MIN_CALLS", 20, int),
            probe_interval_s=_num(env, "PROBE_INTERVAL_S", 300.0, float),
            auth_rest_s=_num(env, "AUTH_REST_S", 900.0, float),
            request_timeout_s=req_timeout,
            resting_after_s=_num(env, "RESTING_AFTER_S", 180.0, float),
            gathering_max_min=_num(env, "GATHERING_MAX_MIN", 30.0, float),
            watchdog_min=_num(env, "WATCHDOG_MIN", 15.0, float),
            status_export_path=_s(env, "STATUS_EXPORT_PATH", "/export/status.json"),
            status_export_every_s=_num(env, "STATUS_EXPORT_EVERY_S", 30.0, float),
            control_path=_s(env, "CONTROL_PATH", "/export/control.json"),
            control_poll_s=_num(env, "CONTROL_POLL_S", 10.0, float),
            sankalpa=SankalpaSettings.from_env(env),
        )
