"""Blind experiments — a sealed identity mapping, revealed only when Chris says so.

The room already had a blind MODE: conversation.blind_labels() derives "Voice N" from
a hash of the session id. That is enough to anonymise a display, but it is not an
experiment: the mapping is RECOMPUTABLE by anyone holding the session id, so nothing is
actually sealed, a second experiment in the same session reuses the same mapping, and
there is nowhere to record when it started, which rounds it covered, or whether it was
compromised.

This module adds the experiment around that mode. The mapping is randomly generated
once, stored, and NOT derivable from the session id. Reveal is manual and recorded.

WHAT IS SEALED, AND FROM WHOM
The mapping lives in memory/blind_experiments.jsonl (mode 0700, like the other stores).
A server administrator can read it — that is unavoidable and is stated plainly rather
than pretended away. What this module guarantees is that the mapping is never handed to
a model, a UI response, an export, or an ordinary log before reveal, and that it cannot
be recomputed from public identifiers.

Append-only, in the house style: reveal and compromise are new records, never edits.
"""

import json
import os
import random
from datetime import datetime, timezone
from typing import Dict, List, Optional

import ledger

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STORE_DIR = os.path.join(BASE_DIR, "memory")
EXPERIMENTS_PATH = os.path.join(STORE_DIR, "blind_experiments.jsonl")

SCHEMA_VERSION = 1

OPENED = "opened"
REVEALED = "revealed"
CLOSED = "closed"

#: Public label format. "Voice N" matches the existing mode text the room already knows.
LABEL_FMT = "Voice {n}"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _append(rec: dict) -> None:
    os.makedirs(STORE_DIR, mode=0o700, exist_ok=True)
    with open(EXPERIMENTS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")
    try:
        os.chmod(EXPERIMENTS_PATH, 0o600)
    except OSError:
        pass


def _read() -> List[dict]:
    out = []
    try:
        with open(EXPERIMENTS_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return out


def _fold(records: List[dict]) -> Dict[str, dict]:
    """Fold the append-only log into current experiment state. Later records supersede
    earlier ones field-by-field; nothing is ever edited in place."""
    state: Dict[str, dict] = {}
    for rec in records:
        eid = rec.get("experiment_id")
        if not eid:
            continue
        cur = state.setdefault(eid, {})
        for k, v in rec.items():
            if k == "_kind":
                continue
            cur[k] = v
    return state


# ---- lifecycle ---------------------------------------------------------------

def open_experiment(session_id: str, participants: List[dict],
                    by: str = "chris") -> dict:
    """Start a new blind experiment with a FRESH random mapping.

    Randomness is deliberate: a deterministic mapping derived from the session id is
    recomputable by anyone who knows the session, which is not a sealed experiment."""
    ids = [p["id"] for p in participants]
    order = list(range(len(ids)))
    random.SystemRandom().shuffle(order)
    mapping = {ids[idx]: LABEL_FMT.format(n=n + 1) for n, idx in enumerate(order)}

    eid = "blind_" + os.urandom(8).hex()
    rec = {
        "_kind": "open",
        "schema": SCHEMA_VERSION,
        "experiment_id": eid,
        "session_id": session_id,
        "status": OPENED,
        "mapping": mapping,             # SEALED — never leaves this module before reveal
        "participant_ids": ids,
        "started_at": _now(),
        "opened_by": by,
        "rounds": [],
        "revealed_at": None,
        "revealed_by": None,
        "compromised": False,
        "leaks": [],
        "config": {"labels": "Voice N", "self_identity_retained": True},
    }
    _append(rec)
    # The ledger records THAT an experiment opened, never the mapping itself.
    ledger.append(by, "blind_experiment_opened", ref=eid,
                  detail={"session_id": session_id, "participants": len(ids)})
    return public_view(eid)


def active_for_session(session_id: str) -> Optional[dict]:
    """The one un-closed experiment for this session, if any (internal — carries the
    mapping). Callers that answer a UI or a model must use public_view()."""
    for eid, exp in _fold(_read()).items():
        if exp.get("session_id") == session_id and exp.get("status") != CLOSED:
            return exp
    return None


def get(experiment_id: str) -> Optional[dict]:
    return _fold(_read()).get(experiment_id)


def label_for(experiment_id: str, participant_id: str) -> Optional[str]:
    exp = get(experiment_id)
    return (exp or {}).get("mapping", {}).get(participant_id)


def record_round(experiment_id: str, session_id: str, round_no: int) -> None:
    exp = get(experiment_id)
    if not exp:
        return
    rounds = list(exp.get("rounds") or [])
    if round_no in rounds:
        return
    rounds.append(round_no)
    _append({"_kind": "round", "experiment_id": experiment_id, "rounds": rounds})


def mark_compromised(experiment_id: str, leaks: List[dict], by: str = "system") -> dict:
    """Record that identity escaped. Never silently — a compromised experiment must be
    visibly compromised, because a blind result nobody can trust is worse than none."""
    exp = get(experiment_id)
    if not exp:
        return {}
    all_leaks = list(exp.get("leaks") or []) + list(leaks or [])
    _append({"_kind": "compromise", "experiment_id": experiment_id,
             "compromised": True, "leaks": all_leaks})
    ledger.append(by, "blind_experiment_compromised", ref=experiment_id,
                  detail={"leak_count": len(all_leaks),
                          "kinds": sorted({l.get("kind") for l in all_leaks})})
    return public_view(experiment_id)


def reveal(experiment_id: str, by: str = "chris") -> dict:
    """Manual reveal. Nothing else in this module ever flips this."""
    exp = get(experiment_id)
    if not exp:
        return {"ok": False, "error": "unknown experiment"}
    if exp.get("status") == REVEALED:
        return {"ok": True, "already": True, **public_view(experiment_id)}
    _append({"_kind": "reveal", "experiment_id": experiment_id,
             "status": REVEALED, "revealed_at": _now(), "revealed_by": by})
    ledger.append(by, "blind_experiment_revealed", ref=experiment_id,
                  detail={"session_id": exp.get("session_id"),
                          "rounds": exp.get("rounds")})
    return {"ok": True, **public_view(experiment_id)}


def close(experiment_id: str, by: str = "chris") -> dict:
    """End blind mode. Closing does NOT reveal — that stays a separate, manual act."""
    _append({"_kind": "close", "experiment_id": experiment_id, "status": CLOSED})
    ledger.append(by, "blind_experiment_closed", ref=experiment_id)
    return public_view(experiment_id)


# ---- views -------------------------------------------------------------------

def public_view(experiment_id: str) -> dict:
    """Everything ABOUT the experiment, and the mapping only once revealed.

    This is the only shape that may reach a UI, an export, or an API response."""
    exp = get(experiment_id)
    if not exp:
        return {}
    revealed = exp.get("status") == REVEALED
    view = {
        "experiment_id": exp.get("experiment_id"),
        "session_id": exp.get("session_id"),
        "status": exp.get("status"),
        "started_at": exp.get("started_at"),
        "opened_by": exp.get("opened_by"),
        "rounds": exp.get("rounds") or [],
        "revealed": revealed,
        "revealed_at": exp.get("revealed_at"),
        "revealed_by": exp.get("revealed_by"),
        "compromised": bool(exp.get("compromised")),
        "leaks": exp.get("leaks") or [],
        "labels": sorted((exp.get("mapping") or {}).values()),
        "config": exp.get("config") or {},
        "sealed": not revealed,
    }
    if revealed:
        # "Participant C — FLINT": the anonymous label kept beside the real identity.
        view["mapping"] = exp.get("mapping") or {}
    return view


def display_map(experiment_id: str, participants: List[dict]) -> Dict[str, str]:
    """id -> what the ROOM shows. Voice N before reveal; 'Voice N — Name' after."""
    exp = get(experiment_id) or {}
    mapping = exp.get("mapping") or {}
    revealed = exp.get("status") == REVEALED
    out = {}
    for p in participants:
        label = mapping.get(p["id"])
        if not label:
            out[p["id"]] = p.get("name") or p["id"]
        elif revealed:
            out[p["id"]] = f"{label} — {p.get('name') or p['id']}"
        else:
            out[p["id"]] = label
    return out
