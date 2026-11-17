import dataclasses
import sys
from datetime import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from akhand.config import Config  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "library.json"


@pytest.fixture
def make_cfg(tmp_path):
    def make(**over):
        env = {
            "DATA_DIR": str(tmp_path / "data"), "LIBRARY_PATH": str(FIXTURE), "PROVIDER": "fake",
            "TARGET_VERSES_PER_AGENT_HOUR": "3600000", "FAKE_LATENCY": "0.05", "TZ": "UTC",
            "STATUS_EXPORT_PATH": str(tmp_path / "no-such-dir" / "status.json"),
            "CONTROL_PATH": str(tmp_path / "no-such-control.json"), "BACKUP_PACED": "0",
        }
        cfg = Config.from_env(env)
        # never let the daily gathering fire during a unit test
        return dataclasses.replace(cfg, gathering_time=time(23, 59), **over)
    return make
