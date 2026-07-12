"""Long-term memory for Team Talk — a JSON file on the server's disk.

The AIs save memories by ending a message with lines like:
    MEMORY: Chris prefers simple UIs with no fluff.
The server strips those lines from the displayed text and stores them
here. Every future conversation (any session) gets the stored memories
injected into its context, so the AIs genuinely remember across sessions.
"""

import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import List, Tuple

import ledger

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MEMORY_DIR = os.path.join(BASE_DIR, "memory")
MEMORY_PATH = os.path.join(MEMORY_DIR, "memory.json")

MAX_ENTRIES = 500          # oldest entries drop off past this
CONTEXT_ENTRIES = 40       # how many recent memories each AI sees
MAX_MEMORY_CHARS = 300     # per saved memory

_MEMORY_LINE = re.compile(r"^[ \t]*MEMORY:[ \t]*(.+?)[ \t]*$", re.MULTILINE)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load() -> List[dict]:
    try:
        with open(MEMORY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def _save(entries: List[dict]) -> None:
    os.makedirs(MEMORY_DIR, mode=0o700, exist_ok=True)
    tmp = f"{MEMORY_PATH}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(entries[-MAX_ENTRIES:], f, indent=2, ensure_ascii=False)
    os.replace(tmp, MEMORY_PATH)


def list_memories() -> List[dict]:
    return _load()


def _record_evictions(entries: List[dict]) -> None:
    """Before the cap truncates the list, leave a ledger tombstone for every
    entry it will drop — so nothing vanishes silently (the room's own rule).
    The content already rode into the ledger at creation; this records that it
    aged out of active memory, which was the hole in the glass box's floor."""
    overflow = len(entries) - MAX_ENTRIES
    for e in entries[:max(0, overflow)]:
        already = e.get("tombstone")
        ledger.append(
            e.get("original_by") or e.get("by") or "system",
            "memory_tombstone_evicted" if already else "memory_evicted",
            ref=e.get("id") or "",
            detail={
                "reason": f"aged out at the {MAX_ENTRIES}-memory cap",
                "text": (e.get("text") or "")[:200],
                "kind": e.get("kind"),
                "created_at": e.get("created_at") or e.get("original_created_at"),
            },
        )


def add(text: str, by: str, kind: str = "ai_observed") -> dict:
    """kind: "chris_stated" (Chris said it directly — fact) or "ai_observed"
    (an AI's own interpretation — carries doubt). Provenance, Splendor-style."""
    text = text.strip()[:MAX_MEMORY_CHARS]
    entry = {"id": uuid.uuid4().hex[:12], "text": text, "by": by,
             "kind": kind if kind in ("chris_stated", "ai_observed") else "ai_observed",
             "created_at": _now()}
    entries = _load()
    entries.append(entry)
    _record_evictions(entries)   # ledger anything the cap is about to drop
    _save(entries)
    return entry


def _tombstone(entry: dict, reason: str, authority: str) -> dict:
    """The content goes. The fact that it existed — and went — does not."""
    return {
        "id": entry.get("id"),
        "tombstone": True,
        "removed_at": _now(),
        "reason": (reason or "removed")[:200],
        "authority": (authority or "Chris")[:60],
        "original_by": entry.get("by"),
        "original_created_at": entry.get("created_at"),
    }


def delete(memory_id: str, reason: str = "removed by Chris via the Memory panel",
           authority: str = "Chris") -> bool:
    entries = _load()
    changed = False
    for i, e in enumerate(entries):
        if e.get("id") == memory_id and not e.get("tombstone"):
            entries[i] = _tombstone(e, reason, authority)
            changed = True
    if changed:
        _save(entries)
    return changed


def clear(reason: str = "Chris cleared all memories", authority: str = "Chris") -> int:
    entries = _load()
    removed = 0
    for i, e in enumerate(entries):
        if not e.get("tombstone"):
            entries[i] = _tombstone(e, reason, authority)
            removed += 1
    _save(entries)
    return removed


def extract_memories(text: str) -> Tuple[str, List[str]]:
    """Pull MEMORY: lines out of an AI response.

    Returns (text with the lines removed, list of memory strings).
    """
    memories = [m.strip() for m in _MEMORY_LINE.findall(text) if m.strip()]
    if not memories:
        return text, []
    cleaned = _MEMORY_LINE.sub("", text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, memories[:2]  # max 2 per message, as instructed


def context_block() -> str:
    """The memory section injected at the top of every AI's context."""
    entries = [e for e in _load() if not e.get("tombstone")][-CONTEXT_ENTRIES:]
    if not entries:
        return ""
    lines = ["=== LONG-TERM MEMORY (saved in past conversations) ==="]
    for e in entries:
        date = e.get("created_at", "")[:10]
        lines.append(f"- [{e.get('by', '?')}, {date}] {e.get('text', '')}")
    return "\n".join(lines)
