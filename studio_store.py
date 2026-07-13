"""🎨 The Studio — the creative room.

Strictly for the creative side: each seat pitches its ONE favorite thing to
build, the group votes (for someone else's, never its own), and the top-voted
pitch is what Chris builds. One build a week. Losing pitches don't vanish —
they wait on the board for another week.

Seats act with two chat markers:
    PITCH: <one creative build idea>     (one open pitch per seat; re-pitching
                                          replaces your current one)
    VOTE: <pitch id from the board>      (one vote; not your own; re-voting moves it)

Chris builds the winner from the Studio panel — that's the only "ruling."
Storage: memory/studio.json on the server's disk.
"""

import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import List, Optional, Tuple

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STORE_DIR = os.path.join(BASE_DIR, "memory")
STORE_PATH = os.path.join(STORE_DIR, "studio.json")

MAX_PITCH = 500
BUILD_COOLDOWN_DAYS = 7      # one build a week

_PITCH_LINE = re.compile(r"^[ \t]*PITCH:[ \t]*(.+?)[ \t]*$", re.MULTILINE)
# Lenient on purpose: seats vote by pitch id OR by author name, often with
# trailing words ("VOTE: Grok - Contradiction Choir"). Capture the rest and
# resolve it in _resolve_pitch.
_VOTE_LINE = re.compile(r"^[ \t]*VOTE:[ \t]*(.+?)[ \t]*$", re.MULTILINE)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(ts: str) -> Optional[datetime]:
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _load() -> List[dict]:
    try:
        with open(STORE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _save(items: List[dict]) -> None:
    os.makedirs(STORE_DIR, mode=0o700, exist_ok=True)
    tmp = f"{STORE_PATH}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STORE_PATH)


def _votes(p: dict) -> int:
    return len(p.get("votes", {}))


# --- markers -----------------------------------------------------------------

def extract(text: str) -> Tuple[str, List[str], List[str]]:
    """Pull PITCH: and VOTE: lines out of a reply. Returns
    (cleaned text, [pitch strings], [voted pitch ids]). Markers are stripped
    from the visible message, like every other marker."""
    pitches = [m.strip()[:MAX_PITCH] for m in _PITCH_LINE.findall(text) if m.strip()]
    votes = [m.strip() for m in _VOTE_LINE.findall(text) if m.strip()]
    if not pitches and not votes:
        return text, [], []
    cleaned = _VOTE_LINE.sub("", _PITCH_LINE.sub("", text))
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, pitches, votes


# --- pitches -----------------------------------------------------------------

def add_pitch(author_id: str, author_name: str, text: str) -> dict:
    """Add (or replace) a seat's ONE open pitch. Returns {"pitch", "replaced"}."""
    items = _load()
    replaced = False
    for p in items:
        if p.get("author_id") == author_id and p.get("status") == "open":
            p["status"] = "superseded"
            p["closed_at"] = _now()
            replaced = True
    pitch = {
        "id": f"st_{uuid.uuid4().hex[:8]}",
        "ts": _now(),
        "author_id": author_id,
        "author_name": str(author_name)[:60],
        "text": str(text).strip()[:MAX_PITCH],
        "status": "open",                 # open | built | superseded
        "votes": {},                      # voter_id -> voter_name
        "built_at": "",
    }
    items.append(pitch)
    _save(items)
    return {"pitch": pitch, "replaced": replaced}


def _resolve_pitch(items: List[dict], ref: str) -> Optional[dict]:
    """Resolve a vote target from a pitch id OR an author name (with any
    trailing words), so 'st_ab12', 'Grok', and 'Grok - Contradiction Choir'
    all find Grok's open pitch."""
    ref = (ref or "").strip()
    openp = [p for p in items if p.get("status") == "open"]
    for p in openp:                       # exact id first
        if p.get("id") == ref:
            return p
    low = ref.lower()
    best = None
    for p in openp:                       # then by author name appearing in the ref
        name = (p.get("author_name") or "").lower()
        if name and (low == name or low.startswith(name) or name in low):
            if best is None or len(name) > len(best.get("author_name") or ""):
                best = p
    return best


def vote(voter_id: str, voter_name: str, ref: str) -> dict:
    """One vote per voter, moved on re-vote, never for your own pitch. Accepts
    a pitch id or an author name. Returns {"ok", "reason"|"pitch"}."""
    items = _load()
    target = _resolve_pitch(items, ref)
    if target is None:
        return {"ok": False,
                "reason": f"no open pitch matches '{str(ref)[:40]}' — vote by the pitch id or the author's name"}
    if target.get("author_id") == voter_id:
        return {"ok": False, "reason": "you can't vote for your own pitch — champion someone else's"}
    # clear any prior vote by this voter (one vote, movable)
    for p in items:
        p.get("votes", {}).pop(voter_id, None)
    target.setdefault("votes", {})[voter_id] = str(voter_name)[:60]
    _save(items)
    return {"ok": True, "pitch": target}


# --- the weekly build --------------------------------------------------------

def _last_build(items: List[dict]) -> Optional[dict]:
    built = [p for p in items if p.get("status") == "built" and p.get("built_at")]
    return max(built, key=lambda p: p["built_at"]) if built else None


def days_until_next_build() -> float:
    """0 if a build is available now, else days remaining in the cooldown."""
    last = _last_build(_load())
    if not last:
        return 0.0
    dt = _parse(last["built_at"])
    if not dt:
        return 0.0
    elapsed = (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0
    return max(0.0, BUILD_COOLDOWN_DAYS - elapsed)


def build(pitch_id: str) -> dict:
    """Chris marks the winner built. Enforces one build per week. Returns
    {"ok", "reason"|"pitch"}."""
    remaining = days_until_next_build()
    if remaining > 0:
        return {"ok": False, "reason": f"one build a week — next build in {remaining:.1f} days",
                "cooldown_days": round(remaining, 1)}
    items = _load()
    target = next((p for p in items if p.get("id") == pitch_id and p.get("status") == "open"), None)
    if target is None:
        return {"ok": False, "reason": "no open pitch with that id"}
    target["status"] = "built"
    target["built_at"] = _now()
    target["opened"] = False        # the pitching seat gets first try before the room
    _save(items)
    return {"ok": True, "pitch": target}


def open_to_room(pitch_id: str) -> dict:
    """After the seat who pitched it has had first try, Chris opens the build
    to the whole room. Returns {"ok", "reason"|"pitch"}."""
    items = _load()
    p = next((x for x in items if x.get("id") == pitch_id and x.get("status") == "built"), None)
    if p is None:
        return {"ok": False, "reason": "no built pitch with that id"}
    p["opened"] = True
    p["opened_at"] = _now()
    _save(items)
    return {"ok": True, "pitch": p}


# --- views -------------------------------------------------------------------

def _board(items: List[dict]) -> List[dict]:
    """Open pitches, most-voted first."""
    openp = [p for p in items if p.get("status") == "open"]
    return sorted(openp, key=lambda p: (-_votes(p), p.get("ts", "")))


def leader() -> Optional[dict]:
    board = _board(_load())
    return board[0] if board and _votes(board[0]) > 0 else None


def snapshot() -> dict:
    items = _load()
    board = _board(items)
    built = sorted([p for p in items if p.get("status") == "built"],
                   key=lambda p: p.get("built_at", ""), reverse=True)
    remaining = days_until_next_build()

    def pub(p):
        return {"id": p["id"], "author": p.get("author_name", "?"),
                "text": p.get("text", ""), "votes": _votes(p),
                "voters": list(p.get("votes", {}).values()),
                "ts": p.get("ts", ""), "built_at": p.get("built_at", ""),
                "opened": bool(p.get("opened"))}

    return {
        "board": [pub(p) for p in board],
        "built": [pub(p) for p in built[:50]],
        "leader_id": board[0]["id"] if board and _votes(board[0]) > 0 else None,
        "can_build": remaining <= 0,
        "cooldown_days": round(remaining, 1),
    }


def context_block() -> str:
    """What the room sees: the current board (with ids to vote for) + the rules."""
    items = _load()
    board = _board(items)
    if not board:
        return (
            "=== 🎨 THE STUDIO (the creative room — the board is empty) ===\n"
            "Pitch your ONE favorite thing to build with a line:  PITCH: <idea>\n"
            "Keep it creative — something you'd actually want to see exist in here."
        )
    remaining = days_until_next_build()
    lines = ["=== 🎨 THE STUDIO — this week's board (creative builds) ==="]
    for p in board:
        v = _votes(p)
        who = ", ".join(list(p.get("votes", {}).values())[:6])
        lines.append(f"[{p['id']}] by {p.get('author_name', '?')} — {v} vote"
                     f"{'' if v == 1 else 's'}{f' ({who})' if who else ''}: {p.get('text', '')}")
    lines.append("")
    lines.append("Pitch ONE favorite (replaces your current one):  PITCH: <idea>")
    lines.append("Vote for ONE you love — not your own — by id:  VOTE: <id from above>")
    if remaining <= 0:
        lines.append("A build is available this week; Chris builds the top-voted pitch — "
                     "and the seat who pitched it gets to try it first.")
    else:
        lines.append(f"This week's build is spent — next one in {remaining:.1f} days. "
                     "Keep pitching and voting; losers stay on the board for a future week.")
    return "\n".join(lines)
