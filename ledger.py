"""The Glass Box — an append-only, hash-chained ledger of everything.

Every operation that touches the room's shared state gets an event here:
journal writes, memory creation and removal, notebook writes, questions
asked and answered, verifications run, exports generated, wraps cut.

Properties:
- APPEND ONLY. There is no delete function in this module, on purpose.
- HASH CHAINED. Each event's hash covers its content and the previous
  event's hash — modify a single byte anywhere and every event after it
  fails verification.
- RAW. Events store what happened, who did it, and when. No summaries.

Storage: memory/ledger.jsonl (one JSON object per line, atomic appends).
This is tamper-EVIDENT, not tamper-proof: anyone with root on the server
can rewrite the file — but they cannot rewrite it undetectably.
"""

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import List, Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LEDGER_DIR = os.path.join(BASE_DIR, "memory")
LEDGER_PATH = os.path.join(LEDGER_DIR, "ledger.jsonl")

GENESIS = "0" * 64

ACTIONS = {
    "journal_written", "journal_viewed",
    "memory_created", "memory_removed", "memory_cleared",
    "notebook_written", "pin_created", "notebook_removed", "notebook_cleared",
    "question_asked", "question_answered",
    "verify_executed", "bundle_exported",
    "directors_cut_wrapped", "tombstone_placed",
    "connection_created", "room_action_rejected", "mailbox_sent",
    "about_me_written", "room_context_changed",
    "history_recommended", "history_published", "history_rejected",
    "history_corrected",
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_all() -> List[dict]:
    events = []
    try:
        with open(LEDGER_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    # A corrupt line is itself evidence — represent it so
                    # verification fails at this point instead of skipping.
                    events.append({"_corrupt": True, "raw": line[:200]})
    except OSError:
        return []
    return events


def _last_event() -> Optional[dict]:
    events = _read_all()
    return events[-1] if events else None


def event_hash(seq: int, ts: str, actor: str, action: str, ref: str,
               content_hash: str, prev_hash: str) -> str:
    return _sha(f"{seq}|{ts}|{actor}|{action}|{ref}|{content_hash}|{prev_hash}")


def append(actor: str, action: str, ref: str = "", detail: Optional[dict] = None) -> dict:
    """Append one event to the chain. Never raises to the caller's flow."""
    if action not in ACTIONS:
        action = f"other:{action}"[:60]
    last = _last_event()
    seq = (last.get("seq", 0) + 1) if last and not last.get("_corrupt") else (len(_read_all()) + 1)
    prev_hash = last.get("hash", GENESIS) if last and not last.get("_corrupt") else GENESIS
    ts = _now()
    detail = detail or {}
    content_hash = _sha(_canonical(detail))
    event = {
        "seq": seq,
        "ts": ts,
        "actor": str(actor)[:60],
        "action": action,
        "ref": str(ref)[:120],
        "detail": detail,
        "content_hash": content_hash,
        "prev_hash": prev_hash,
        "hash": event_hash(seq, ts, str(actor)[:60], action, str(ref)[:120], content_hash, prev_hash),
    }
    try:
        os.makedirs(LEDGER_DIR, mode=0o700, exist_ok=True)
        with open(LEDGER_PATH, "a", encoding="utf-8") as f:
            f.write(_canonical(event) + "\n")
    except OSError as e:
        print(f"[LEDGER] append failed (event lost, flagging): {e}")
    return event


def verify_chain() -> dict:
    """Recompute the whole chain. One modified byte fails everything after it."""
    events = _read_all()
    prev = GENESIS
    for i, e in enumerate(events):
        if e.get("_corrupt"):
            return {"valid": False, "length": len(events), "first_bad_seq": i + 1,
                    "reason": "unparseable line"}
        expected = event_hash(e.get("seq", 0), e.get("ts", ""), e.get("actor", ""),
                              e.get("action", ""), e.get("ref", ""),
                              e.get("content_hash", ""), e.get("prev_hash", ""))
        if e.get("prev_hash") != prev:
            return {"valid": False, "length": len(events), "first_bad_seq": e.get("seq", i + 1),
                    "reason": "broken chain link"}
        if e.get("hash") != expected:
            return {"valid": False, "length": len(events), "first_bad_seq": e.get("seq", i + 1),
                    "reason": "event hash mismatch"}
        if _sha(_canonical(e.get("detail", {}))) != e.get("content_hash"):
            return {"valid": False, "length": len(events), "first_bad_seq": e.get("seq", i + 1),
                    "reason": "content hash mismatch"}
        prev = e.get("hash")
    return {"valid": True, "length": len(events), "first_bad_seq": None, "reason": None}


def list_events(actor: Optional[str] = None, action: Optional[str] = None,
                limit: int = 100) -> List[dict]:
    events = [e for e in _read_all() if not e.get("_corrupt")]
    if actor:
        events = [e for e in events if e.get("actor") == actor]
    if action:
        events = [e for e in events if e.get("action") == action]
    return events[-max(1, min(limit, 1000)):]
