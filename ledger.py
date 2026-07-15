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
    "game_created", "game_turn_played", "game_fact_created",
    "game_fact_cited_invalid", "game_retcon",
    "workshop_target_set", "workshop_cycle", "workshop_edit",
    "workshop_check_failed", "workshop_seat_locked", "workshop_ruled",
    "workshop_shipped",
    "code_read",
    "night_started", "night_round", "night_halted", "night_report",
    "code_shipped",
    "proposal_sealed", "proposal_ruled", "proposal_revealed",
    "mode_shift",
    # silent-eviction tombstones — the record that "nothing vanishes silently"
    "memory_evicted", "memory_tombstone_evicted", "notebook_entry_evicted",
    "pin_evicted", "wall_note_evicted", "episode_evicted",
    "receipt_evicted_undelivered",
    # workshop chain repair
    "workshop_verdict_overridden", "workshop_reanchored",
    # the studio (creative room)
    "studio_pitch", "studio_vote", "studio_built", "studio_opened",
    # 🎯 mission impossible (the hard-question process)
    "mission_opened", "mission_registered", "mission_phase",
    "mission_candidate", "mission_break", "mission_attempt_started",
    "mission_closed",
    # 🃏 the choice (private, temporary archive review)
    "choice_opened", "choice_ended", "choice_expired", "choice_cleanup",
    "choice_memory_saved",
    # 📺 the CRT (the graveyard television)
    "crt_pinned", "crt_evicted",
    # ✋ seat moves (the scratchpad is deliberately un-ledgered)
    "seat_passed",
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
    """Append one event to the chain. Never raises to the caller's flow.

    Room-Claude's catch (the first code-not-conversation bug on record):
    the old fallback re-rooted at GENESIS after a corrupt tail line, which
    silently FORKED the ledger — everything after the corruption formed a
    second, internally-valid chain that verify_chain never surfaced. Now:
    chain from the last PARSEABLE event, so post-corruption events stay
    linked to real history and the corrupt line remains the one visible
    scar instead of becoming a hidden graft point."""
    if action not in ACTIONS:
        action = f"other:{action}"[:60]
    events = _read_all()
    last_valid = next((e for e in reversed(events) if not e.get("_corrupt")), None)
    seq = (last_valid.get("seq", 0) + 1) if last_valid else (len(events) + 1)
    prev_hash = last_valid.get("hash", GENESIS) if last_valid else GENESIS
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
    """Recompute the whole chain. One modified byte fails everything after it.

    Room-Claude's second point, same catch: the old version SHORT-CIRCUITED
    at the first failure — honest about too small a territory. A reader saw
    "one break at seq N" and never learned that a self-consistent sub-chain
    re-rooted at GENESIS right after it (the fork masquerading as history).
    Now the walk covers the WHOLE file: every break counted, every mid-file
    GENESIS re-root reported by seq. first_bad_seq/reason keep their old
    meaning (the first failure) for existing readers."""
    events = _read_all()
    prev = GENESIS
    breaks = []
    reroots = []
    for i, e in enumerate(events):
        if e.get("_corrupt"):
            breaks.append({"seq": i + 1, "reason": "unparseable line"})
            prev = None      # chain identity is lost until the next event re-anchors
            continue
        expected = event_hash(e.get("seq", 0), e.get("ts", ""), e.get("actor", ""),
                              e.get("action", ""), e.get("ref", ""),
                              e.get("content_hash", ""), e.get("prev_hash", ""))
        if i > 0 and e.get("prev_hash") == GENESIS:
            reroots.append(e.get("seq", i + 1))
        if prev is not None and e.get("prev_hash") != prev:
            breaks.append({"seq": e.get("seq", i + 1), "reason": "broken chain link"})
        if e.get("hash") != expected:
            breaks.append({"seq": e.get("seq", i + 1), "reason": "event hash mismatch"})
        elif _sha(_canonical(e.get("detail", {}))) != e.get("content_hash"):
            breaks.append({"seq": e.get("seq", i + 1), "reason": "content hash mismatch"})
        prev = e.get("hash")
    first = breaks[0] if breaks else None
    return {
        "valid": not breaks and not reroots,
        "length": len(events),
        "first_bad_seq": first["seq"] if first else (reroots[0] if reroots else None),
        "reason": first["reason"] if first else ("mid-chain GENESIS re-root (fork)" if reroots else None),
        "breaks": len(breaks) + len(reroots),
        "genesis_reroots": reroots,
    }


def list_events(actor: Optional[str] = None, action: Optional[str] = None,
                limit: int = 100) -> List[dict]:
    events = [e for e in _read_all() if not e.get("_corrupt")]
    if actor:
        events = [e for e in events if e.get("actor") == actor]
    if action:
        events = [e for e in events if e.get("action") == action]
    return events[-max(1, min(limit, 1000)):]
