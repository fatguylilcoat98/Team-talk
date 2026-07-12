"""🔀 SHIFT TO — the seats' power to change the room's mode, one round
at a time. Designed by the room itself on Night Shift #2 (7 rounds, 13
dissents, four-seat lock) and shipped to their spec:

- Marker: `SHIFT TO: [mode]` on its own line. Stripped and receipted.
- Takes effect NEXT round, lasts exactly ONE round, then auto-returns
  to Chris's floor. No seat may deactivate or extend.
- Chris's floor is immutable: if his mode pick changes, a pending seat
  shift is dropped (REJECTED: conflict). Two seats in one round: the
  earliest in turn order wins; later attempts are REJECTED: conflict.
- Caps (SUCCESS only — rejected attempts cost nothing, Claude R7):
  global 5 per session (joy/voice modes only), per-seat 2 per session,
  3-round cooldown. The cap-exempt whitelist is FROZEN AT SHIP TIME
  and names shipped modes only — {chain_auditor} — because "a
  whitelist that names nonexistent modes is a hole disguised as a
  fence" (the report) and seats self-certifying into the free lane was
  the attack (Claude R4/R6). Only Chris promotes into it.
- blind / courtroom / hard_truth are Chris-only: a SHIFT TO one of
  them fails closed (privileged), and NO shift fires while the floor
  IS one of them (privileged-floor — Claude's de-anonymization catch).
- Every attempt is recorded:
  >> MODE SHIFT: [mode] | BY: [seat] | STATUS: ... | REASON: ... | RETURNS: [floor]
"""

import re
from typing import List, Optional, Tuple

MARKER_RE = re.compile(r"^[ \t]*SHIFT TO:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)

EXCLUDED = {"blind", "courtroom", "hard_truth"}
CAP_EXEMPT = {"chain_auditor"}       # frozen at ship; only Chris promotes
GLOBAL_CAP = 5
SEAT_CAP = 2
COOLDOWN_ROUNDS = 3


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (name or "").strip().lower()).strip("_")


def extract(text: str) -> Tuple[str, List[str]]:
    """Strip every SHIFT TO line; return (cleaned, [requested slugs])."""
    found = [slug(m) for m in MARKER_RE.findall(text or "")]
    cleaned = MARKER_RE.sub("", text or "").strip()
    return cleaned, [f for f in found if f]


def _state(session: dict) -> dict:
    return session.setdefault("mode_shift", {
        "global_used": 0,            # joy/voice successes only
        "seat_used": {},             # seat_id -> successes
        "seat_last": {},             # seat_id -> round of last success
        "pending": None,             # {"mode","by","by_id","for_round"}
    })


def record_line(rec: dict) -> str:
    return (f">> MODE SHIFT: {rec['mode']} | BY: {rec['by']} | "
            f"STATUS: {rec['status']} | REASON: {rec['reason']} | "
            f"RETURNS: {rec.get('returns', '?')}")


def attempt(session: dict, seat_id: str, seat_name: str, requested: str,
            floor_modes: List[str], round_number: int,
            known_modes: set) -> dict:
    """One seat's SHIFT TO. Evaluates the room's rules in order; only a
    SUCCESS mutates any counter (rejections are free — no grief lever)."""
    st = _state(session)
    floor = "+".join(floor_modes) or "collab"
    rec = {"mode": requested, "by": seat_name, "by_id": seat_id,
           "round": round_number, "status": "REJECTED", "reason": "",
           "returns": floor}

    if requested not in known_modes:
        rec["reason"] = "unknown-mode"
        return rec
    if requested in EXCLUDED:
        rec["reason"] = "privileged"
        return rec
    if any(m in EXCLUDED for m in floor_modes):
        rec["reason"] = "privileged-floor"
        return rec
    if st["pending"] is not None:
        rec["reason"] = "conflict"       # earlier seat in turn order won
        return rec
    if st["seat_used"].get(seat_id, 0) >= SEAT_CAP:
        rec["reason"] = "cap"
        return rec
    last = st["seat_last"].get(seat_id)
    if last is not None and round_number < last + COOLDOWN_ROUNDS:
        rec["reason"] = "cap"            # cooldown counts as cap per spec enum
        return rec
    if requested not in CAP_EXEMPT and st["global_used"] >= GLOBAL_CAP:
        rec["reason"] = "cap"
        return rec

    st["seat_used"][seat_id] = st["seat_used"].get(seat_id, 0) + 1
    st["seat_last"][seat_id] = round_number
    if requested not in CAP_EXEMPT:
        st["global_used"] += 1
    st["pending"] = {"mode": requested, "by": seat_name, "by_id": seat_id,
                     "for_round": round_number + 1, "floor": floor}
    rec["status"] = "SUCCESS"
    rec["reason"] = "none"
    return rec


def apply_pending(session: dict, request_modes: List[str],
                  prev_modes: List[str], round_number: int) -> Optional[dict]:
    """Called at the top of a chat round. Returns None (no shift), or
    {"modes": [...], "record": {...}} where record says what happened.
    Chris's same-round pick beats the seat: if his mode selection
    changed since the shift was filed, the shift drops (conflict)."""
    st = _state(session)
    pending = st.get("pending")
    if not pending:
        return None
    st["pending"] = None                      # one shot, no matter what
    if pending.get("for_round") != round_number:
        return None                           # stale (round never came)
    rec = {"mode": pending["mode"], "by": pending["by"],
           "by_id": pending["by_id"], "round": round_number,
           "returns": pending.get("floor", "collab")}
    if sorted(request_modes or []) != sorted(prev_modes or []):
        rec.update(status="REJECTED", reason="conflict")   # Chris's pick wins
        return {"modes": None, "record": rec}
    rec.update(status="SUCCESS", reason="none")
    return {"modes": [pending["mode"]], "record": rec}
