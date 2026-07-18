"""Focused tests for the failure_log fcntl-availability guard.

Standalone (no pytest): `python tests/test_failure_log_locking.py`. Runs on
any OS: the fcntl module reference is swapped in-process to cover both the
Unix (fcntl present) and Windows (fcntl absent) paths deterministically.
The log path is re-pointed at a temp dir so nothing touches real logs.
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import failure_log as FL


class _FakeFcntl:
    """Records flock ops so we can assert lock/unlock happened."""
    LOCK_EX = 2
    LOCK_UN = 8

    def __init__(self):
        self.calls = []

    def flock(self, fd, op):
        self.calls.append(op)


def _point(d):
    FL.LOG_DIR = d
    FL.LOG_PATH = os.path.join(d, "failures.jsonl")


def _entry(call_id):
    return {"call_id": call_id, "seat": "s", "provider": "p", "model": "m",
            "error_class": "timeout"}


def test_unix_path_calls_lock_and_unlock_when_fcntl_available():
    """With fcntl available, the exact LOCK_EX / LOCK_UN calls still fire."""
    d = tempfile.mkdtemp()
    saved = FL.fcntl
    try:
        _point(d)
        fake = _FakeFcntl()
        FL.fcntl = fake
        FL.log_attempt(_entry("unix1"))
        # lock then unlock, in that order — behavior unchanged from Unix
        assert fake.calls == [fake.LOCK_EX, fake.LOCK_UN], fake.calls
        with open(FL.LOG_PATH, encoding="utf-8") as f:
            assert '"call_id":"unix1"' in f.read()
    finally:
        FL.fcntl = saved
        shutil.rmtree(d, ignore_errors=True)


def test_windows_path_imports_and_writes_when_fcntl_unavailable():
    """With fcntl absent (fcntl is None), the module still writes the line
    and never raises — locking degrades to a no-op."""
    d = tempfile.mkdtemp()
    saved = FL.fcntl
    try:
        _point(d)
        FL.fcntl = None                       # simulate Windows
        FL.log_attempt(_entry("win1"))        # must not raise
        with open(FL.LOG_PATH, encoding="utf-8") as f:
            assert '"call_id":"win1"' in f.read()
    finally:
        FL.fcntl = saved
        shutil.rmtree(d, ignore_errors=True)


ALL_TESTS = [
    test_unix_path_calls_lock_and_unlock_when_fcntl_available,
    test_windows_path_imports_and_writes_when_fcntl_unavailable,
]


if __name__ == "__main__":
    for t in ALL_TESTS:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\nALL PASS ({len(ALL_TESTS)} tests)")
