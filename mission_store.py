"""🎯 Mission Impossible — the hard-question process.

A structured gauntlet for pointing the whole council at a genuinely hard (maybe
unsolvable) question and getting HONEST progress out of it — not a fake "AI
solved it," but a candidate that survived the room's own worst attempt to break
it, stamped with the full record of how it got there.

The process is a fixed sequence of phases. The room can run the gauntlet as many
times as it takes — each full pass is an ATTEMPT — and every phase change is a
ledger event, so the genealogy of an idea is never lost.

    0. register      — pre-register what would COUNT as progress, and SEAL it
                       (so the goalposts can't move after the fact)
    1. discovery     — blind, independent idea generation; range wide, don't converge
    2. construction  — build the single strongest candidate; ground it in the
                       literature before calling anything novel
    3. red_team      — everyone switches sides and tries to BREAK the candidate;
                       scored on breaks found, not agreement
    4. verification  — a checker rules if one exists (no charm); otherwise state
                       honestly what survived. Output = a candidate for HUMAN
                       review, NEVER a claim of solution.

The only "rulings" are Chris advancing the phase and closing the mission. The
room's actual work (proposals, candidates, breaks) rides in on normal chat.

Storage: memory/missions.json on the server's disk.
"""

import json
import os
import uuid
from datetime import datetime, timezone
from typing import List, Optional

import ledger

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STORE_DIR = os.path.join(BASE_DIR, "memory")
STORE_PATH = os.path.join(STORE_DIR, "missions.json")

MAX_QUESTION = 2000
MAX_TEXT = 6000

# The fixed phase order. "closed" is a terminal status, not a phase.
PHASES = ["register", "discovery", "construction", "red_team", "verification"]

PHASE_TITLE = {
    "register": "Phase 0 · Pre-register the win",
    "discovery": "Phase 1 · Discovery (blind)",
    "construction": "Phase 2 · Construction",
    "red_team": "Phase 3 · Red Team",
    "verification": "Phase 4 · Verification",
}

# What the room is actually told to do in each phase — the process, encoded.
PHASE_ORDERS = {
    "register": (
        "PHASE 0 — PRE-REGISTER THE WIN. Before touching the problem, agree on "
        "what would count as REAL progress on THIS specific question, and be "
        "concrete and falsifiable: an explicit construction? a bound improved? a "
        "hidden assumption exposed? a common line of attack disproved? a lemma "
        "formalized? Then SEAL it with a  PROPOSAL: <the win criteria>  line so "
        "it's committed and the goalposts can't move later. Do not start solving "
        "yet — just define what winning means."
    ),
    "discovery": (
        "PHASE 1 — DISCOVERY. Think independently first; do NOT converge yet. "
        "Range as wide as you can: angles, analogies, reformulations, what "
        "exactly makes this hard, where it connects to known results. No idea is "
        "too rough. Diversity of approach is the whole point of a council — don't "
        "collapse onto whoever spoke first."
    ),
    "construction": (
        "PHASE 2 — CONSTRUCTION. Build the single STRONGEST candidate out of the "
        "discovery pile. Before you call anything novel, ground it in the "
        "literature — say where it might already exist; a rediscovered known "
        "result dressed up as new is the classic failure. State every assumption "
        "explicitly. Make the candidate precise enough to attack."
    ),
    "red_team": (
        "PHASE 3 — RED TEAM. Switch sides. Your job now is to BREAK the "
        "candidate — find the flawed step, the hidden assumption, the "
        "counterexample, the known refutation. You are scored on breaks found, "
        "NOT on agreement. Agreement between models is not evidence; a real "
        "attack is. If you cannot break it, say precisely why the attack failed."
    ),
    "verification": (
        "PHASE 4 — VERIFICATION. If a checker exists (code, a proof assistant, a "
        "solver, an experiment), it rules — no charm, no eloquence. If there is "
        "no mechanical checker, state HONESTLY: did the candidate survive the "
        "red team? What exactly survived, and what is still unverified? The "
        "output is a candidate worth a HUMAN expert's time — never a claim that "
        "the problem is solved."
    ),
}

OUTCOMES = {
    "survived_for_review": "survived the red team — a candidate for human review (NOT verified as solved)",
    "checker_passed": "a mechanical checker ruled it correct",
    "partial_progress": "the problem stands, but real partial progress was made",
    "dead_end": "no progress this run — a logged dead end (still a record of how it was explored)",
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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


def _active(items: List[dict]) -> Optional[dict]:
    return next((m for m in items if m.get("status") == "active"), None)


def _new_attempt(n: int) -> dict:
    return {"n": n, "started_at": _now(), "candidate": "",
            "candidate_at": "", "breaks": [], "verdict": None}


# --- lifecycle ---------------------------------------------------------------

def create(question: str, checker_mode: str = "none", checker_note: str = "") -> dict:
    """Open a new mission. Only one is active at a time — opening a new one
    shelves any active mission as 'closed' with a dead_end outcome unless it was
    already closed."""
    question = (question or "").strip()[:MAX_QUESTION]
    if not question:
        raise ValueError("a mission needs a question")
    items = _load()
    # Only one active mission at a time; park any current one.
    active = _active(items)
    if active:
        active["status"] = "closed"
        active["outcome"] = active.get("outcome") or "dead_end"
        active["closed_at"] = _now()
        ledger.append("Chris", "mission_closed", ref=active["id"],
                      detail={"outcome": active["outcome"], "reason": "shelved for a new mission"})
    checker_mode = checker_mode if checker_mode in ("script", "manual", "none") else "none"
    mission = {
        "id": f"mi_{uuid.uuid4().hex[:8]}",
        "ts": _now(),
        "question": question,
        "checker_mode": checker_mode,          # script | manual | none
        "checker_note": str(checker_note)[:MAX_TEXT],
        "status": "active",                    # active | closed
        "phase": "register",
        "registration": {"criteria": "", "sealed": False, "commitment": "", "sealed_at": ""},
        "attempts": [_new_attempt(1)],
        "outcome": None,
        "closed_at": "",
    }
    items.append(mission)
    _save(items)
    ledger.append("Chris", "mission_opened", ref=mission["id"],
                  detail={"question": question[:200], "checker": checker_mode})
    return mission


def seal_registration(criteria: str, commitment: str = "") -> dict:
    """Lock in what counts as progress. Records a sealed pre-registration so the
    goalposts can't move after the room sees how hard the problem is."""
    items = _load()
    m = _active(items)
    if not m:
        return {"ok": False, "reason": "no active mission"}
    if m["phase"] != "register":
        return {"ok": False, "reason": f"registration is Phase 0 — this mission is at {m['phase']}"}
    criteria = (criteria or "").strip()[:MAX_TEXT]
    if not criteria:
        return {"ok": False, "reason": "the win criteria can't be empty"}
    m["registration"] = {"criteria": criteria, "sealed": True,
                         "commitment": str(commitment)[:80], "sealed_at": _now()}
    _save(items)
    ledger.append("Chris", "mission_registered", ref=m["id"],
                  detail={"criteria": criteria[:400]})
    return {"ok": True, "mission": m}


def advance() -> dict:
    """Move to the next phase. From verification, advancing starts a NEW attempt
    back at discovery (the gauntlet of tries). Register → discovery requires the
    win criteria to be sealed first."""
    items = _load()
    m = _active(items)
    if not m:
        return {"ok": False, "reason": "no active mission"}
    cur = m["phase"]
    if cur == "register" and not m.get("registration", {}).get("sealed"):
        return {"ok": False, "reason": "seal the win criteria (Phase 0) before starting Discovery"}
    if cur == "verification":
        # a fresh attempt at the gauntlet
        n = len(m["attempts"]) + 1
        m["attempts"].append(_new_attempt(n))
        m["phase"] = "discovery"
        _save(items)
        ledger.append("Chris", "mission_attempt_started", ref=m["id"], detail={"attempt": n})
        return {"ok": True, "mission": m, "phase": "discovery", "attempt": n}
    idx = PHASES.index(cur)
    m["phase"] = PHASES[idx + 1]
    _save(items)
    ledger.append("Chris", "mission_phase", ref=m["id"],
                  detail={"from": cur, "to": m["phase"], "attempt": len(m["attempts"])})
    return {"ok": True, "mission": m, "phase": m["phase"]}


def set_candidate(text: str) -> dict:
    """Record the current attempt's strongest candidate (Construction output)."""
    items = _load()
    m = _active(items)
    if not m:
        return {"ok": False, "reason": "no active mission"}
    att = m["attempts"][-1]
    att["candidate"] = (text or "").strip()[:MAX_TEXT]
    att["candidate_at"] = _now()
    _save(items)
    ledger.append("Chris", "mission_candidate", ref=m["id"],
                  detail={"attempt": att["n"], "text": att["candidate"][:400]})
    return {"ok": True, "mission": m}


def add_break(by: str, text: str) -> dict:
    """Log a red-team break against the current candidate."""
    items = _load()
    m = _active(items)
    if not m:
        return {"ok": False, "reason": "no active mission"}
    text = (text or "").strip()[:MAX_TEXT]
    if not text:
        return {"ok": False, "reason": "an empty break is not a break"}
    att = m["attempts"][-1]
    att["breaks"].append({"by": str(by)[:60], "text": text, "ts": _now()})
    _save(items)
    ledger.append(str(by)[:60], "mission_break", ref=m["id"],
                  detail={"attempt": att["n"], "text": text[:400]})
    return {"ok": True, "mission": m}


def close(outcome: str, note: str = "") -> dict:
    """Close the mission with an honest outcome and record a verdict on the
    current attempt."""
    if outcome not in OUTCOMES:
        return {"ok": False, "reason": f"outcome must be one of: {', '.join(OUTCOMES)}"}
    items = _load()
    m = _active(items)
    if not m:
        return {"ok": False, "reason": "no active mission"}
    m["attempts"][-1]["verdict"] = {"outcome": outcome, "note": str(note)[:MAX_TEXT], "ts": _now()}
    m["status"] = "closed"
    m["outcome"] = outcome
    m["closed_at"] = _now()
    _save(items)
    ledger.append("Chris", "mission_closed", ref=m["id"],
                  detail={"outcome": outcome, "note": str(note)[:400],
                          "attempts": len(m["attempts"])})
    return {"ok": True, "mission": m}


# --- views -------------------------------------------------------------------

def _pub(m: dict) -> dict:
    att = m["attempts"][-1] if m.get("attempts") else None
    return {
        "id": m["id"], "question": m["question"], "status": m["status"],
        "phase": m["phase"], "phase_title": PHASE_TITLE.get(m["phase"], m["phase"]),
        "checker_mode": m.get("checker_mode", "none"),
        "checker_note": m.get("checker_note", ""),
        "registration": m.get("registration", {}),
        "attempt_count": len(m.get("attempts", [])),
        "current_attempt": att,
        "attempts": m.get("attempts", []),
        "outcome": m.get("outcome"),
        "orders": PHASE_ORDERS.get(m["phase"], "") if m["status"] == "active" else "",
        "ts": m.get("ts", ""), "closed_at": m.get("closed_at", ""),
    }


def snapshot() -> dict:
    items = _load()
    active = _active(items)
    closed = [m for m in items if m.get("status") == "closed"]
    closed.sort(key=lambda m: m.get("closed_at", ""), reverse=True)
    return {
        "active": _pub(active) if active else None,
        "closed": [_pub(m) for m in closed[:50]],
        "phases": PHASES,
        "outcomes": OUTCOMES,
    }


def context_block() -> str:
    """What the room sees mid-mission: the question, the sealed win criteria, the
    current phase, and that phase's marching orders."""
    m = _active(_load())
    if not m:
        return ""
    att = m["attempts"][-1]
    lines = ["=== 🎯 MISSION IMPOSSIBLE — a hard question, run as a process ===",
             f"THE QUESTION: {m['question']}"]
    reg = m.get("registration", {})
    if reg.get("sealed"):
        lines.append(f"SEALED WIN CRITERIA (locked in Phase 0): {reg.get('criteria', '')}")
    lines.append(f"ATTEMPT #{att['n']} · {PHASE_TITLE.get(m['phase'], m['phase'])}")
    if att.get("candidate") and m["phase"] in ("red_team", "verification"):
        lines.append(f"THE CANDIDATE ON THE TABLE: {att['candidate']}")
    if att.get("breaks") and m["phase"] in ("red_team", "verification"):
        lines.append(f"BREAKS LOGGED SO FAR: {len(att['breaks'])} — a surviving candidate must answer every one.")
    if m.get("checker_mode") in ("script", "manual"):
        note = m.get("checker_note") or "a checker will rule at verification"
        lines.append(f"CHECKER: {m['checker_mode']} — {note}")
    lines.append("")
    lines.append(PHASE_ORDERS.get(m["phase"], ""))
    lines.append("Golden rule: never claim the problem is solved. The honest win is a "
                 "candidate that survived your own worst attack, marked for human review.")
    return "\n".join(lines)


def export_text() -> Optional[str]:
    """A human-review export of the active (or most recent) mission: the full
    genealogy, honestly labelled. The ledger holds the tamper-evident version;
    this is the readable one."""
    items = _load()
    m = _active(items)
    if not m:
        closed = [x for x in items if x.get("status") == "closed"]
        m = max(closed, key=lambda x: x.get("closed_at", ""), default=None)
    if not m:
        return None
    L = [f"MISSION IMPOSSIBLE — {m['id']}",
         f"Question: {m['question']}",
         f"Status: {m['status']}"
         + (f" · outcome: {OUTCOMES.get(m['outcome'], m['outcome'])}" if m.get("outcome") else ""),
         ""]
    reg = m.get("registration", {})
    if reg.get("sealed"):
        L += [f"Pre-registered win criteria (sealed {reg.get('sealed_at','')}):",
              f"  {reg.get('criteria','')}", ""]
    for att in m.get("attempts", []):
        L.append(f"— Attempt #{att['n']} (started {att.get('started_at','')}) —")
        if att.get("candidate"):
            L.append(f"  Candidate: {att['candidate']}")
        for b in att.get("breaks", []):
            L.append(f"  Break by {b.get('by','?')}: {b.get('text','')}")
        v = att.get("verdict")
        if v:
            L.append(f"  Verdict: {OUTCOMES.get(v['outcome'], v['outcome'])}"
                     + (f" — {v['note']}" if v.get("note") else ""))
        L.append("")
    L.append("NOT A CLAIM OF SOLUTION. This is a candidate that survived the room's own "
             "red team, exported for a human expert to verify or refute.")
    return "\n".join(L)
