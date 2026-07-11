"""The failure log — structured record of every failed AI call attempt.

Specced by the room itself on Night Shift run #1 (their first unattended
work), refined in the Living Room after the oracle's verdict. The rules
implemented here are theirs, to the letter:

- logs/failures.jsonl, JSONL, append-only. Each entry is ONE atomic
  write on an O_APPEND descriptor under flock — two seats failing in the
  same millisecond cannot interleave bytes.
- A failed attempt is written IMMEDIATELY (crash loses nothing — the
  batch-buffer alternative was proposed and rejected on exactly that
  ground, night #1 R2).
- recovered:true is written iff ≥1 prior recovered:false exists for the
  same call_id in the same invocation. A clean first-attempt success
  writes ZERO lines — this is a failure log, not a call log.
- Rotation at >5MB to logs/archive/failures.YYYYMMDD-HHMMSS.jsonl,
  10 newest archives kept. Because rotation destroys the time window,
  counts read from here are labeled "failures in current log window" —
  never "24h/7d" (Claude's R3 dissent: don't promise a window the
  retention policy can't back).
"""

import fcntl
import json
import os
from datetime import datetime, timezone
from typing import Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")
LOG_PATH = os.path.join(LOG_DIR, "failures.jsonl")
ARCHIVE_DIR = os.path.join(LOG_DIR, "archive")

MAX_BYTES = int(os.getenv("FAILURE_LOG_MAX_BYTES", str(5 * 1024 * 1024)))
KEEP_ARCHIVES = 10

FIELDS = ("ts_utc", "call_id", "run_id", "session_id", "seat", "provider",
          "model", "error_class", "http_status", "attempt", "max_attempts",
          "final", "recovered", "latency_ms", "retry_after_used",
          "budget_blocked", "retry_after_exceeded_ceiling", "msg_trunc")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log_attempt(entry: dict) -> None:
    """One atomic line. Never raises into the caller's flow — a broken
    log must not break the call it was recording."""
    row = {k: entry.get(k) for k in FIELDS}
    row["ts_utc"] = row["ts_utc"] or _now()
    row["msg_trunc"] = str(row.get("msg_trunc") or "")[:200]
    line = (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    try:
        os.makedirs(LOG_DIR, mode=0o700, exist_ok=True)
        fd = os.open(LOG_PATH, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            os.write(fd, line)          # single syscall — no interleaving
            size = os.fstat(fd).st_size
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        if size > MAX_BYTES:
            _rotate()
    except OSError as e:
        print(f"[FAILLOG] write failed (entry lost): {e}")


def _rotate() -> None:
    try:
        os.makedirs(ARCHIVE_DIR, mode=0o700, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        os.replace(LOG_PATH, os.path.join(ARCHIVE_DIR, f"failures.{stamp}.jsonl"))
        archives = sorted(n for n in os.listdir(ARCHIVE_DIR)
                          if n.startswith("failures.") and n.endswith(".jsonl"))
        for old in archives[:-KEEP_ARCHIVES]:
            os.remove(os.path.join(ARCHIVE_DIR, old))
    except OSError as e:
        print(f"[FAILLOG] rotation failed: {e}")


def _read_live() -> list:
    rows = []
    try:
        with open(LOG_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return rows


def window_counts() -> dict:
    """Per-seat counts from the LIVE file only. The label is part of the
    contract: after a rotation this resets, and it must say so."""
    rows = _read_live()
    seats = {}
    for r in rows:
        seat = r.get("seat") or "?"
        s = seats.setdefault(seat, {"failed_attempts": 0, "final_failures": 0,
                                    "recovered": 0, "by_class": {}})
        if r.get("recovered"):
            s["recovered"] += 1
            continue
        s["failed_attempts"] += 1
        if r.get("final"):
            s["final_failures"] += 1
        cls = r.get("error_class") or "?"
        s["by_class"][cls] = s["by_class"].get(cls, 0) + 1
    return {"label": "failures in current log window",
            "entries": len(rows), "seats": seats}


def recent(limit: int = 50) -> list:
    return _read_live()[-max(1, min(limit, 500)):]
