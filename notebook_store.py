"""The Notebook — a shared scratchpad all the AIs (and Chris) write to.

The room asked for this itself: raw entries in each AI's own words, not
one AI's summary of everyone. Two kinds of writing, both stored in
memory/notebook.json on the server's disk and shown to every AI at the
start of every round:

  NOTEBOOK: <raw thought, in the writer's own words>   (Claude's ask)
  PIN: <an exact quote from the conversation>          (Grok's ask)

The server strips those lines from the displayed reply and stores them
here, exactly like MEMORY lines.
"""

import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import List, Tuple

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
NOTEBOOK_DIR = os.path.join(BASE_DIR, "memory")
NOTEBOOK_PATH = os.path.join(NOTEBOOK_DIR, "notebook.json")

MAX_ENTRIES = 300          # oldest notebook entries drop off past this
MAX_PINS = 100
CONTEXT_ENTRIES = 25       # recent notebook entries each AI sees
CONTEXT_PINS = 20
MAX_ENTRY_CHARS = 500
MAX_PIN_CHARS = 300

_NOTEBOOK_LINE = re.compile(r"^[ \t]*NOTEBOOK:[ \t]*(.+?)[ \t]*$", re.MULTILINE)
_PIN_LINE = re.compile(r"^[ \t]*PIN:[ \t]*(.+?)[ \t]*$", re.MULTILINE)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load() -> dict:
    try:
        with open(NOTEBOOK_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"entries": [], "pins": []}
    if not isinstance(data, dict):
        return {"entries": [], "pins": []}
    return {
        "entries": data.get("entries") or [],
        "pins": data.get("pins") or [],
    }


def _save(data: dict) -> None:
    os.makedirs(NOTEBOOK_DIR, mode=0o700, exist_ok=True)
    data = {
        "entries": data["entries"][-MAX_ENTRIES:],
        "pins": data["pins"][-MAX_PINS:],
    }
    tmp = f"{NOTEBOOK_PATH}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, NOTEBOOK_PATH)


def list_all() -> dict:
    return _load()


def add_entry(text: str, by: str) -> dict:
    entry = {"id": uuid.uuid4().hex[:12], "text": text.strip()[:MAX_ENTRY_CHARS],
             "by": by, "created_at": _now()}
    data = _load()
    data["entries"].append(entry)
    _save(data)
    return entry


def add_pin(text: str, by: str) -> dict:
    pin = {"id": uuid.uuid4().hex[:12], "text": text.strip()[:MAX_PIN_CHARS],
           "by": by, "created_at": _now()}
    data = _load()
    data["pins"].append(pin)
    _save(data)
    return pin


def _tombstone(item: dict, reason: str, authority: str) -> dict:
    """Content may disappear. History never does."""
    return {
        "id": item.get("id"),
        "tombstone": True,
        "removed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "reason": (reason or "removed")[:200],
        "authority": (authority or "Chris")[:60],
        "original_by": item.get("by"),
        "original_created_at": item.get("created_at"),
    }


def delete_entry(entry_id: str, reason: str = "removed by Chris via the Notebook panel",
                 authority: str = "Chris") -> bool:
    data = _load()
    changed = False
    for i, e in enumerate(data["entries"]):
        if e.get("id") == entry_id and not e.get("tombstone"):
            data["entries"][i] = _tombstone(e, reason, authority)
            changed = True
    if changed:
        _save(data)
    return changed


def delete_pin(pin_id: str, reason: str = "removed by Chris via the Notebook panel",
               authority: str = "Chris") -> bool:
    data = _load()
    changed = False
    for i, p in enumerate(data["pins"]):
        if p.get("id") == pin_id and not p.get("tombstone"):
            data["pins"][i] = _tombstone(p, reason, authority)
            changed = True
    if changed:
        _save(data)
    return changed


def clear(reason: str = "Chris cleared the notebook", authority: str = "Chris") -> int:
    data = _load()
    removed = 0
    for key in ("entries", "pins"):
        for i, item in enumerate(data[key]):
            if not item.get("tombstone"):
                data[key][i] = _tombstone(item, reason, authority)
                removed += 1
    _save(data)
    return removed


def extract(text: str) -> Tuple[str, List[str], List[str]]:
    """Pull NOTEBOOK: and PIN: lines out of an AI response.

    Returns (text with the lines removed, notebook entries, pins).
    """
    entries = [m.strip() for m in _NOTEBOOK_LINE.findall(text) if m.strip()]
    pins = [m.strip() for m in _PIN_LINE.findall(text) if m.strip()]
    if not entries and not pins:
        return text, [], []
    cleaned = _NOTEBOOK_LINE.sub("", text)
    cleaned = _PIN_LINE.sub("", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, entries[:3], pins[:2]


def context_block() -> str:
    """The notebook + pinned quotes sections injected into every context."""
    data = _load()
    lines = []
    import brain
    pins = [p for p in data["pins"] if not p.get("tombstone")][-CONTEXT_PINS:]
    if pins:
        lines.append("=== PINNED QUOTES (exact lines the room chose to keep) ===")
        for p in pins:
            when = brain.fmt_ts(p.get("created_at", ""))
            lines.append(f'- "{p.get("text", "")}" (pinned by {p.get("by", "?")}, {when})')
    entries = [e for e in data["entries"] if not e.get("tombstone")][-CONTEXT_ENTRIES:]
    if entries:
        if lines:
            lines.append("")
        lines.append("=== THE NOTEBOOK (shared scratchpad — everyone's own raw words) ===")
        for e in entries:
            when = brain.fmt_ts(e.get("created_at", ""))
            lines.append(f"- [{e.get('by', '?')}, {when}] {e.get('text', '')}")
    return "\n".join(lines)
