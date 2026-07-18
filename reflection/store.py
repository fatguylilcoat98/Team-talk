"""Append-only store for ReflectionResult objects.

Separate from the reasoning ledger and session files — this module never reads
or writes either. One JSON object per line under memory/reflections.jsonl,
atomic single-line appends, no update, no delete. Every write is best-effort:
a failure logs and returns None rather than raising into the send path. A
malformed historical line never blocks a future append.

Privacy: only the bounded excerpts already inside warnings are stored — full
messages and prompts are never copied here.
"""

import json
import os
from datetime import datetime, timezone
from typing import List, Optional

from .models import ReflectionResult

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORE_DIR = os.path.join(BASE_DIR, "memory")
REFLECTIONS_PATH = os.path.join(STORE_DIR, "reflections.jsonl")


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def record(result, revision_performed: bool = None) -> Optional[dict]:
    """Append one reflection. Accepts a ReflectionResult or a plain dict.
    Returns the stored dict, or None on write failure (never raises)."""
    entry = result.to_dict() if isinstance(result, ReflectionResult) else dict(result)
    entry.setdefault("timestamp", _now())
    if revision_performed is not None:
        entry["revision_performed"] = bool(revision_performed)
    try:
        os.makedirs(STORE_DIR, mode=0o700, exist_ok=True)
        with open(REFLECTIONS_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")
    except OSError as e:
        print(f"[reflection] store write failed (dropped, not fatal): {e}")
        return None
    return entry


def _read_all() -> List[dict]:
    out = []
    try:
        with open(REFLECTIONS_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    out.append({"_corrupt": True})       # tolerated, not fatal
    except OSError:
        return []
    return out


def list_reflections(author: str = None, limit: int = 500) -> List[dict]:
    rows = [r for r in _read_all() if not r.get("_corrupt")]
    if author:
        rows = [r for r in rows if r.get("author") == author]
    return rows[-max(1, min(limit, 5000)):]


def analytics() -> dict:
    """Read-only derived metrics; never persisted."""
    rows = [r for r in _read_all() if not r.get("_corrupt")]
    by_sev = {"green": 0, "yellow": 0, "red": 0}
    by_cat, by_pass, authors_warned = {}, {}, set()
    durations, red, yellow, revised = [], 0, 0, 0
    for r in rows:
        by_sev[r.get("overall_severity", "green")] = by_sev.get(r.get("overall_severity", "green"), 0) + 1
        if r.get("overall_severity") == "red":
            red += 1
        elif r.get("overall_severity") == "yellow":
            yellow += 1
        if r.get("revision_performed"):
            revised += 1
        if isinstance(r.get("duration_ms"), (int, float)):
            durations.append(r["duration_ms"])
        warned = False
        for pr in r.get("pass_results", []):
            by_pass[pr.get("pass_name")] = by_pass.get(pr.get("pass_name"), 0) + len(pr.get("warnings", []))
        for w in r.get("warnings", []):
            by_cat[w.get("category")] = by_cat.get(w.get("category"), 0) + 1
            warned = True
        if warned and r.get("author"):
            authors_warned.add(r["author"])
    return {
        "total_reflections": len(rows),
        "by_severity": by_sev,
        "green": by_sev["green"], "yellow": yellow, "red": red,
        "warnings_by_category": by_cat,
        "reflections_by_pass": by_pass,
        "authors_warned": sorted(authors_warned),
        "revisions_performed": revised,
        "avg_duration_ms": round(sum(durations) / len(durations), 4) if durations else 0.0,
    }
