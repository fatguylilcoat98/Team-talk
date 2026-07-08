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


def add(text: str, by: str) -> dict:
    text = text.strip()[:MAX_MEMORY_CHARS]
    entry = {"id": uuid.uuid4().hex[:12], "text": text, "by": by, "created_at": _now()}
    entries = _load()
    entries.append(entry)
    _save(entries)
    return entry


def delete(memory_id: str) -> bool:
    entries = _load()
    kept = [e for e in entries if e.get("id") != memory_id]
    if len(kept) == len(entries):
        return False
    _save(kept)
    return True


def clear() -> int:
    entries = _load()
    _save([])
    return len(entries)


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
    entries = _load()[-CONTEXT_ENTRIES:]
    if not entries:
        return ""
    lines = ["=== LONG-TERM MEMORY (saved in past conversations) ==="]
    for e in entries:
        date = e.get("created_at", "")[:10]
        lines.append(f"- [{e.get('by', '?')}, {date}] {e.get('text', '')}")
    return "\n".join(lines)
