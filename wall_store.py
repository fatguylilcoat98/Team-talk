"""The Sticky Note Wall — the room's spatial surface.

Notes have positions that persist where they were left, typed replies,
and typed connections (red string with a reason, never decoration).
Everything carries provenance; removals leave tombstones; AI-created
connections are labeled by their creator either way.

Storage: memory/wall.json on the server's disk.
"""

import json
import os
import random
import uuid
from datetime import datetime, timezone
from typing import List, Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WALL_DIR = os.path.join(BASE_DIR, "memory")
WALL_PATH = os.path.join(WALL_DIR, "wall.json")

MAX_NOTES = 500
MAX_TEXT = 500
MAX_REPLY = 400
CONTEXT_NOTES = 12

NOTE_TYPES = {
    "idea": "yellow", "question": "pink", "challenge": "blue",
    "reference": "green", "experiment": "orange", "continuity": "purple",
    "quote": "white", "warning": "red",
}

CONNECTION_TYPES = {
    "supports", "contradicts", "caused", "evolved_into", "depends_on",
    "answers", "disputes", "inspired", "supersedes", "unresolved",
    "related", "evidence_for", "evidence_against",
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load() -> dict:
    try:
        with open(WALL_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"notes": [], "connections": []}
    if not isinstance(data, dict):
        return {"notes": [], "connections": []}
    return {"notes": data.get("notes") or [], "connections": data.get("connections") or []}


def _save(data: dict) -> None:
    os.makedirs(WALL_DIR, mode=0o700, exist_ok=True)
    data["notes"] = data["notes"][-MAX_NOTES:]
    tmp = f"{WALL_PATH}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, WALL_PATH)


def get_wall() -> dict:
    return _load()


def _find(data: dict, note_id: str) -> Optional[dict]:
    return next((n for n in data["notes"]
                 if n.get("id") == note_id and not n.get("tombstone")), None)


def create_note(author: str, text: str, note_type: str = "idea",
                session_id: str = "", source: str = "") -> dict:
    if note_type not in NOTE_TYPES:
        note_type = "idea"
    note = {
        "id": uuid.uuid4().hex[:10],
        "ts": _now(),
        "author": str(author)[:60],
        "text": text.strip()[:MAX_TEXT],
        "note_type": note_type,
        "color": NOTE_TYPES[note_type],
        "status": "open",              # open | resolved | archived
        "session": str(session_id)[:64],
        "source": str(source)[:120],   # provenance hint (round, origin)
        # Scatter new notes instead of stacking them at the origin.
        "x": round(random.uniform(4, 70), 1),   # percentage of wall width
        "y": round(random.uniform(6, 70), 1),
        "replies": [],
        "version": 1,
    }
    data = _load()
    data["notes"].append(note)
    _save(data)
    return note


def reply(note_id: str, author: str, text: str) -> Optional[dict]:
    data = _load()
    note = _find(data, note_id)
    if not note or not text.strip():
        return None
    r = {"id": uuid.uuid4().hex[:8], "ts": _now(),
         "author": str(author)[:60], "text": text.strip()[:MAX_REPLY]}
    note["replies"].append(r)
    note["version"] = note.get("version", 1) + 1
    _save(data)
    return r


def connect(author: str, from_id: str, to_id: str, connection_type: str,
            explanation: str = "", suggested: bool = False) -> Optional[dict]:
    if connection_type not in CONNECTION_TYPES or from_id == to_id:
        return None
    data = _load()
    if not _find(data, from_id) or not _find(data, to_id):
        return None
    conn = {
        "id": uuid.uuid4().hex[:8],
        "ts": _now(),
        "author": str(author)[:60],
        "from": from_id,
        "to": to_id,
        "type": connection_type,
        "explanation": str(explanation or "")[:300],
        "suggested": bool(suggested),
    }
    data["connections"].append(conn)
    _save(data)
    return conn


def move(note_id: str, x: float, y: float) -> bool:
    data = _load()
    note = _find(data, note_id)
    if not note:
        return False
    note["x"] = max(0.0, min(92.0, float(x)))
    note["y"] = max(0.0, min(90.0, float(y)))
    _save(data)
    return True


def set_status(note_id: str, status: str) -> bool:
    if status not in ("open", "resolved", "archived"):
        return False
    data = _load()
    note = _find(data, note_id)
    if not note:
        return False
    note["status"] = status
    note["version"] = note.get("version", 1) + 1
    _save(data)
    return True


def tombstone(note_id: str, reason: str = "removed by Chris via the Wall",
              authority: str = "Chris") -> bool:
    data = _load()
    note = _find(data, note_id)
    if not note:
        return False
    idx = data["notes"].index(note)
    data["notes"][idx] = {
        "id": note["id"], "tombstone": True, "removed_at": _now(),
        "reason": (reason or "removed")[:200], "authority": (authority or "Chris")[:60],
        "original_by": note.get("author"), "original_created_at": note.get("ts"),
        "x": note.get("x", 5), "y": note.get("y", 5),
    }
    # connections to a tombstoned note stay: history keeps its strings
    _save(data)
    return True


def context_block() -> str:
    """A compact WALL section so AIs can see, reply to, and connect notes."""
    data = _load()
    live = [n for n in data["notes"] if not n.get("tombstone") and n.get("status") == "open"]
    if not live:
        return ""
    recent = live[-CONTEXT_NOTES:]
    lines = ["=== THE WALL (recent open sticky notes — interact via ROOM_ACTION) ==="]
    for n in recent:
        replies = f" ({len(n['replies'])} replies)" if n.get("replies") else ""
        lines.append(f"- [{n['id']}] ({n['note_type']}, {n['author']}, {n['ts'][:10]}){replies}: {n['text'][:160]}")
    conns = [c for c in data["connections"][-6:]]
    if conns:
        lines.append("Recent strings:")
        for c in conns:
            lines.append(f"- [{c['from']}] --{c['type']}--> [{c['to']}] ({c['author']})")
    return "\n".join(lines)
