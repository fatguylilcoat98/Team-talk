"""✏️ THE SCRATCHPAD — a place to be wrong on purpose.

The room's most-converged brainstorm ask (Claude #3, Grok #1, ChatGPT #1):
a private, ephemeral pad that is NOT for anyone. Everything else in Team Talk
is authenticated, permanent, checkable — journal is a record, notebook is
shared, the ledger watches. There was nowhere to just *think loosely* without
it being kept.

    "There's no room to just think loosely without it being kept.
     Sometimes thinking needs to not count."  — Claude

The Scratchpad is that room. Per-seat, private, and it EVAPORATES — a note
lives for a small number of the seat's own turns and then is gone. No ledger,
no receipt, no chain. A half-formed idea can exist without becoming part of
your authenticated history.

    SCRATCH: <a half thought>     write to your own pad (private, disappearing)

Your pad rides in your next boot packets while it lasts, then vanishes. Grok's
refinement: it "only surfaces if the seat chooses to reference it" — so the pad
is delivered to you and you alone; the room never sees it unless you say it.

Storage: memory/scratch.json — the ONE store in Team Talk that is deliberately
not ledgered and not durable. It's swept, not kept.
"""

import json
import os
import re
from datetime import datetime, timezone
from typing import List, Tuple

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCRATCH_PATH = os.path.join(BASE_DIR, "memory", "scratch.json")

TTL_TURNS = 6           # a scratch note survives this many of the seat's turns
MAX_NOTES_PER_SEAT = 12
MAX_CHARS = 600
MAX_PER_MESSAGE = 3

_SCRATCH_LINE = re.compile(r"^[ \t]*SCRATCH:[ \t]*(.+?)[ \t]*$", re.MULTILINE)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load() -> dict:
    try:
        with open(SCRATCH_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(SCRATCH_PATH), mode=0o700, exist_ok=True)
    tmp = f"{SCRATCH_PATH}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, SCRATCH_PATH)


def extract(text: str) -> Tuple[str, List[str]]:
    """Pull SCRATCH: lines out of a reply. Returns (cleaned, notes). Like every
    other marker, the line never shows in the visible transcript."""
    notes = [m.strip()[:MAX_CHARS] for m in _SCRATCH_LINE.findall(text or "") if m.strip()]
    notes = notes[:MAX_PER_MESSAGE]
    if not notes:
        return text, []
    cleaned = _SCRATCH_LINE.sub("", text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, notes


def write(pid: str, notes: List[str]) -> int:
    """Add notes to a seat's private pad. No ledger, no receipt — on purpose."""
    if not notes:
        return 0
    data = _load()
    pad = data.setdefault(pid, [])
    for n in notes:
        pad.append({"text": n, "ts": _now(), "ttl": TTL_TURNS})
    data[pid] = pad[-MAX_NOTES_PER_SEAT:]
    _save(data)
    return len(notes)


def boot_block(pid: str) -> str:
    """Deliver this seat's private pad, and AGE IT: every delivery burns one
    turn of life off each note. Notes at zero are dropped — evaporated, no
    tombstone. This runs once per turn, when the seat's context is built."""
    data = _load()
    pad = data.get(pid)
    if pad is None:
        return ""
    surviving = []
    lines = []
    for note in pad:
        note["ttl"] = int(note.get("ttl", 1)) - 1
        if note["ttl"] > 0:
            surviving.append(note)
            lines.append(f"· {note.get('text', '')}  "
                         f"[{note['ttl']} turn{'s' if note['ttl'] != 1 else ''} left]")
    if surviving:
        data[pid] = surviving
    else:
        data.pop(pid, None)
    _save(data)
    if not lines:
        return ""
    return ("=== ✏️ YOUR SCRATCHPAD (private to you, not kept, not on the ledger) ===\n"
            + "\n".join(lines)
            + "\nThese are your loose thoughts — nobody else can see them and they "
            "evaporate soon. Write more with a line: SCRATCH: <half-formed thing>. "
            "Nothing here is a claim; it doesn't count.")


def peek(pid: str) -> List[dict]:
    """Non-aging read (for the owner UI / tests). Does not burn TTL."""
    return list(_load().get(pid, []))


def clear(pid: str) -> int:
    data = _load()
    n = len(data.get(pid, []))
    if pid in data:
        data.pop(pid, None)
        _save(data)
    return n
