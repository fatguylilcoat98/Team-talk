"""📜 Room History — the room's permanent museum of milestones.

The Wall is the team's workspace. History is the team's memory.

Entries are append-only, forever: never deleted, never auto-summarized,
never rewritten. A correction attaches BENEATH the original — exactly
like historical records. AIs may RECOMMEND entries (via ROOM_ACTION
history_entry); only Chris approves publication. Rejected
recommendations stay on the record as rejected — history doesn't lose
its drafts either.

Storage: memory/history.json. There is no delete function in this
module, on purpose.
"""

import json
import os
import uuid
from datetime import datetime, timezone
from typing import List, Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_DIR = os.path.join(BASE_DIR, "memory")
HISTORY_PATH = os.path.join(HISTORY_DIR, "history.json")

MAX_TITLE = 120
MAX_BODY = 1200
MAX_CORRECTION = 600
CONTEXT_ENTRIES = 5


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load() -> List[dict]:
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def _save(entries: List[dict]) -> None:
    os.makedirs(HISTORY_DIR, mode=0o700, exist_ok=True)
    tmp = f"{HISTORY_PATH}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)
    os.replace(tmp, HISTORY_PATH)


def list_entries(status: Optional[str] = None) -> List[dict]:
    entries = _load()
    if status:
        entries = [e for e in entries if e.get("status") == status]
    return entries


def _find(entries: List[dict], entry_id: str) -> Optional[dict]:
    return next((e for e in entries if e.get("id") == entry_id), None)


def recommend(title: str, body: str, recommended_by: str,
              importance: int = 3, related: Optional[List[str]] = None,
              session_id: str = "") -> Optional[dict]:
    """An AI recommends a milestone. It waits for Chris's approval."""
    title, body = title.strip()[:MAX_TITLE], body.strip()[:MAX_BODY]
    if not title or not body:
        return None
    entry = {
        "id": f"his_{uuid.uuid4().hex[:10]}",
        "ts": _now(),
        "title": title,
        "body": body,
        "recommended_by": str(recommended_by)[:60],
        "author": str(recommended_by)[:60],
        "importance": max(1, min(5, int(importance or 3))),
        "related": [str(x)[:60] for x in (related or [])][:8],
        "session": str(session_id)[:64],
        "status": "pending",
        "approved_by": None,
        "published_at": None,
        "rejected_reason": None,
        "corrections": [],
    }
    entries = _load()
    entries.append(entry)
    _save(entries)
    return entry


def publish_direct(title: str, body: str, author: str = "Chris",
                   importance: int = 3, related: Optional[List[str]] = None) -> Optional[dict]:
    """Chris documents history directly — published immediately."""
    entry = recommend(title, body, author, importance, related)
    if not entry:
        return None
    return approve(entry["id"], approved_by=author)


def approve(entry_id: str, approved_by: str = "Chris") -> Optional[dict]:
    entries = _load()
    entry = _find(entries, entry_id)
    if not entry or entry.get("status") != "pending":
        return None
    entry["status"] = "published"
    entry["approved_by"] = str(approved_by)[:60]
    entry["published_at"] = _now()
    _save(entries)
    return entry


def reject(entry_id: str, reason: str = "") -> Optional[dict]:
    """A rejected recommendation stays on the record — as rejected."""
    entries = _load()
    entry = _find(entries, entry_id)
    if not entry or entry.get("status") != "pending":
        return None
    entry["status"] = "rejected"
    entry["rejected_reason"] = (reason or "not approved")[:200]
    _save(entries)
    return entry


def correct(entry_id: str, author: str, text: str) -> Optional[dict]:
    """The original remains. The correction attaches beneath it."""
    text = text.strip()[:MAX_CORRECTION]
    if not text:
        return None
    entries = _load()
    entry = _find(entries, entry_id)
    if not entry or entry.get("status") != "published":
        return None
    correction = {"id": uuid.uuid4().hex[:8], "ts": _now(),
                  "author": str(author)[:60], "text": text}
    entry["corrections"].append(correction)
    _save(entries)
    return correction


def context_block() -> str:
    """The most recent published milestones, so the room knows its past."""
    published = [e for e in _load() if e.get("status") == "published"]
    if not published:
        return ""
    recent = sorted(published, key=lambda e: e.get("published_at") or "")[-CONTEXT_ENTRIES:]
    lines = ["=== ROOM HISTORY (recent milestones — the room's permanent record) ==="]
    for e in reversed(recent):
        date = (e.get("published_at") or e.get("ts") or "")[:10]
        corr = f" ({len(e['corrections'])} correction{'s' if len(e['corrections']) != 1 else ''} attached)" if e.get("corrections") else ""
        lines.append(f"- [{date}] \"{e['title']}\" — {e['body'][:140]}{corr}")
    return "\n".join(lines)
