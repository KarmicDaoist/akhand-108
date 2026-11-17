"""Single-instance lock in DATA_DIR (released by the OS when the process dies)."""
from __future__ import annotations

import os
from pathlib import Path


class AlreadyRunning(Exception):
    pass


class InstanceLock:
    def __init__(self, path: Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o644)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(self.fd)
            raise AlreadyRunning(f"another akhand process holds {path}")
        os.ftruncate(self.fd, 0)
        os.write(self.fd, str(os.getpid()).encode())

    def release(self):
        try:
            if os.name == "nt":
                import msvcrt
                os.lseek(self.fd, 0, 0)
                msvcrt.locking(self.fd, msvcrt.LK_UNLCK, 1)
            os.close(self.fd)
        except OSError:
            pass
