"""A machine-wide cap on concurrent model calls, shared by every WhyChain process (app, CLI, evals).

A per-process limit doesn't stop bursts when several processes run at once, which is what pushed NVIDIA's API into
429s. Each slot is a lock file in the temp directory, held with an OS-level exclusive lock for the length of one
call; a crashed process releases its lock automatically.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
import time
from contextlib import contextmanager

if os.name == "nt":
    import msvcrt

    def _try_lock(fh) -> bool:
        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _unlock(fh) -> None:
        try:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
else:
    import fcntl

    def _try_lock(fh) -> bool:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def _unlock(fh) -> None:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


DIR = os.path.join(tempfile.gettempdir(), "whychain-slots")


@contextmanager
def slot(key: str, n: int, wait_s: float = 600.0):
    """Hold one of `n` machine-wide slots for `key` (e.g. an API endpoint). Waits up to `wait_s`, then raises."""
    os.makedirs(DIR, exist_ok=True)
    tag = hashlib.sha1(key.encode()).hexdigest()[:10]
    deadline = time.monotonic() + wait_s
    while True:
        for i in range(max(n, 1)):
            fh = open(os.path.join(DIR, f"{tag}-{i}.lock"), "a+")
            fh.seek(0)
            if _try_lock(fh):
                try:
                    yield i
                finally:
                    _unlock(fh)
                    fh.close()
                return
            fh.close()
        if time.monotonic() > deadline:
            raise TimeoutError(f"no free model-call slot for {wait_s:.0f}s")
        time.sleep(0.25)
