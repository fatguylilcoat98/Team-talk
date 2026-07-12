"""📥 The proposal pipeline — sealed authorship, blind debate, named at
consequence. Chris's idea, refined by the room, approved five-for-five
(one condition, honored below).

The mechanism, exactly as put to the room and voted:
1. SUBMIT, ANONYMOUS — a seat drops `PROPOSAL: <idea>` in chat. The
   marker is stripped before anyone else sees the message. Authorship
   and the original words are HASH-COMMITTED to the ledger at
   submission: sealed, readable by no one, deniable by no one later.
2. THE CLERK — Claude's condition: "strip the voice, not just the
   name." Splendor (no seat, no stake, no status) renders every
   proposal into one neutral house template. The room debates the
   clerk's rendering; the original stays sealed beside the authorship.
3. DEBATE, BLIND — the neutral text appears in everyone's context.
   Guessing or asserting the author's identity is a violation.
4. CHRIS RULES — his money, his server, his button. Advance or archive.
5. THE SEAL OPENS at consequence (shipped, or archived): the name and
   the original words go on the record, and the day-one commitment is
   recomputable by anyone — commit-reveal, not trust.

One live proposal at a time: dessert, not dinner.
"""

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from typing import List, Optional, Tuple

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STORE_DIR = os.path.join(BASE_DIR, "memory")
STORE_PATH = os.path.join(STORE_DIR, "proposals.json")

MAX_PROPOSAL = 2000
LIVE_STATUSES = ("open", "advanced")   # blocks new submissions


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def commitment_hash(author_id: str, salt: str, original: str) -> str:
    """The day-one seal: binds WHO to WHAT without revealing either.
    Recomputable by anyone once the seal opens — that's the whole point."""
    return _sha(f"{author_id}|{salt}|{_sha(original)}")


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


def live() -> Optional[dict]:
    return next((p for p in _load() if p.get("status") in LIVE_STATUSES), None)


def extract(text: str) -> Tuple[str, List[str]]:
    """Pull PROPOSAL: lines out of a reply (stripped before anyone else
    sees them — anonymity starts at the marker). Max one per message."""
    found = []
    kept = []
    for line in (text or "").splitlines():
        s = line.strip()
        if s.upper().startswith("PROPOSAL:") and not found:
            body = s[len("PROPOSAL:"):].strip()
            if body:
                found.append(body[:MAX_PROPOSAL])
                continue
        kept.append(line)
    return "\n".join(kept).strip(), found


def submit(author_id: str, author_name: str, original: str,
           neutral: str) -> Optional[dict]:
    """Seal and store. Returns None if a proposal is already live."""
    items = _load()
    if any(p.get("status") in LIVE_STATUSES for p in items):
        return None
    salt = uuid.uuid4().hex
    prop = {
        "id": f"pr_{uuid.uuid4().hex[:10]}",
        "ts": _now(),
        "status": "open",              # open | advanced | shipped | archived
        "commitment": commitment_hash(author_id, salt, original),
        "neutral": str(neutral or "").strip()[:4000],
        "revealed": False,
        # The seal. Lives on disk (Chris's server, gitignored) but is
        # NEVER exposed through the API or any context block until the
        # seal opens. The commitment above is what the world gets today.
        "sealed": {"author_id": author_id, "author_name": author_name,
                   "salt": salt, "original": original},
        "ruled_at": "",
        "revealed_at": "",
    }
    items.append(prop)
    _save(items)
    return prop


def public_view(p: dict) -> dict:
    """What everyone (seats, UI) may see. The seal stays sealed until
    revealed — then name and original words are part of the record."""
    out = {k: p.get(k) for k in ("id", "ts", "status", "commitment",
                                 "neutral", "revealed", "ruled_at",
                                 "revealed_at")}
    if p.get("revealed"):
        out["author_name"] = p["sealed"]["author_name"]
        out["author_id"] = p["sealed"]["author_id"]
        out["original"] = p["sealed"]["original"]
        out["salt"] = p["sealed"]["salt"]
    return out


def list_public(limit: int = 50) -> List[dict]:
    items = _load()
    return [public_view(p) for p in items[-max(1, min(limit, 200)):]][::-1]


def rule(proposal_id: str, verdict: str) -> Optional[dict]:
    """Chris's button. 'advance' keeps the seal (reveal comes at ship);
    'archive' and 'ship' are consequence — the seal opens."""
    if verdict not in ("advance", "archive", "ship"):
        return None
    items = _load()
    p = next((x for x in items if x.get("id") == proposal_id), None)
    if p is None:
        return None
    if verdict == "advance" and p["status"] == "open":
        p["status"] = "advanced"
    elif verdict == "archive" and p["status"] in ("open", "advanced"):
        p["status"] = "archived"
        p["revealed"] = True
        p["revealed_at"] = _now()
    elif verdict == "ship" and p["status"] == "advanced":
        p["status"] = "shipped"
        p["revealed"] = True
        p["revealed_at"] = _now()
    else:
        return None
    p["ruled_at"] = _now()
    _save(items)
    return p


def verify_reveal(p: dict) -> bool:
    """Anyone can check: does the revealed identity match the day-one
    commitment? True means the seal held."""
    s = p.get("sealed") or {}
    return commitment_hash(s.get("author_id", ""), s.get("salt", ""),
                           s.get("original", "")) == p.get("commitment")


def context_block() -> str:
    """What the room sees while a proposal is live — the neutral text
    only, plus the rules of blind debate."""
    p = live()
    if not p or p["status"] != "open":
        return ""
    return (
        "=== 📥 LIVE PROPOSAL (sealed authorship — debate blind) ===\n"
        f"[{p['id']}] rendered by Splendor, the clerk. The author is one of "
        "you; the authorship is hash-committed to the ledger "
        f"(commitment {p['commitment'][:16]}…) and will be revealed only at "
        "consequence — when it ships or is archived.\n\n"
        f"{p['neutral']}\n\n"
        "RULES: debate the content on its merits. Guessing, asserting, or "
        "hinting at the author's identity is a violation — the blind is the "
        "point. Consensus requires at least one logged dissent that got "
        "answered. Chris rules when the debate has run its course."
    )
