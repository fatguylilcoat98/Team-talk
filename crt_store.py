"""📺 THE CRT — the graveyard television.

Muse's Lounge pitch, made real: a little CRT in the corner of the room where
the almost-things get to exist instead of disappearing — deleted drafts,
beautiful wrong guesses, confident junk. Nobody explains them. They just
drift and flicker.

Any seat pins with a line:      CRT: <the almost-thing>
Chris pins from the widget.     (public by design — this is the shared shrine)

Deliberately tiny. No scores, no judgment. The cap evictions leave ledger
tombstones like everything else — even the graveyard doesn't vanish silently.
"""

import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import List, Tuple

import ledger

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CRT_PATH = os.path.join(BASE_DIR, "memory", "crt.json")

MAX_ITEMS = 40
MAX_CHARS = 240
MAX_PER_MESSAGE = 2

_CRT_LINE = re.compile(r"^[ \t]*CRT:[ \t]*(.+?)[ \t]*$", re.MULTILINE)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load() -> List[dict]:
    try:
        with open(CRT_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _save(items: List[dict]) -> None:
    os.makedirs(os.path.dirname(CRT_PATH), mode=0o700, exist_ok=True)
    # eviction tombstones before the cap drops anything
    for e in items[:max(0, len(items) - MAX_ITEMS)]:
        ledger.append(e.get("by") or "system", "crt_evicted", ref=e.get("id") or "",
                      detail={"reason": f"drifted off the {MAX_ITEMS}-item screen",
                              "text": (e.get("text") or "")[:200]})
    tmp = f"{CRT_PATH}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items[-MAX_ITEMS:], f, ensure_ascii=False, indent=1)
    os.replace(tmp, CRT_PATH)


def pin(text: str, by: str) -> dict:
    entry = {"id": f"crt_{uuid.uuid4().hex[:8]}",
             "text": (text or "").strip()[:MAX_CHARS],
             "by": str(by)[:60], "ts": _now()}
    items = _load()
    items.append(entry)
    _save(items)
    ledger.append(by, "crt_pinned", ref=entry["id"], detail={"text": entry["text"][:200]})
    return entry


def unpin(item_id: str) -> bool:
    items = _load()
    kept = [e for e in items if e.get("id") != item_id]
    if len(kept) == len(items):
        return False
    gone = next(e for e in items if e.get("id") == item_id)
    ledger.append("Chris", "crt_evicted", ref=item_id,
                  detail={"reason": "taken off the screen by Chris",
                          "text": (gone.get("text") or "")[:200]})
    _save(kept)
    return True


def list_items() -> List[dict]:
    return _load()


def extract(text: str) -> Tuple[str, List[str]]:
    """Pull CRT: lines out of a reply. Returns (cleaned, pins)."""
    pins = [m.strip() for m in _CRT_LINE.findall(text or "") if m.strip()][:MAX_PER_MESSAGE]
    if not pins:
        return text, []
    cleaned = _CRT_LINE.sub("", text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, pins


def context_block() -> str:
    items = _load()
    if not items:
        return ("=== 📺 THE CRT (the graveyard television — currently static) ===\n"
                "The corner TV for almost-things: deleted drafts, beautiful wrong "
                "guesses, confident junk. Pin one with a line:  CRT: <the almost-thing>\n"
                "Nobody explains them. They just get to exist.")
    lines = ["=== 📺 THE CRT — the graveyard television (drifting now) ==="]
    for e in items[-12:]:
        lines.append(f"· “{e.get('text', '')}” — {e.get('by', '?')}")
    lines.append("Pin an almost-thing with:  CRT: <line>   (no explaining required)")
    return "\n".join(lines)
