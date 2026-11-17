"""Entry point: python -m akhand.main"""
from __future__ import annotations

import asyncio
import logging
import signal
import sys

from .config import Config
from .harness import Harness
from .library import LibraryError
from .lock import AlreadyRunning

EXIT_LOCKED, EXIT_CONFIG = 3, 5


async def _amain(cfg: Config) -> int:
    h = Harness(cfg)
    loop = asyncio.get_running_loop()
    for name in ("SIGTERM", "SIGINT", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, h.request_stop)
        except (NotImplementedError, RuntimeError):
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(h.request_stop))
    return await h.run()


def main() -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)
    cfg = Config.from_env()
    if cfg.provider == "opencode" and not cfg.free[0].api_key:
        print("OPENCODE_GO_API_KEY (or OPENCODE_API_KEY) is not set", file=sys.stderr)
        return EXIT_CONFIG
    try:
        return asyncio.run(_amain(cfg))
    except AlreadyRunning as e:
        print(f"refusing to start: {e}", file=sys.stderr)
        return EXIT_LOCKED
    except LibraryError as e:
        print(f"library problem: {e}", file=sys.stderr)
        return EXIT_CONFIG


if __name__ == "__main__":
    sys.exit(main())
