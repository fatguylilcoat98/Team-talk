"""Reasoning graph — Layer 0: persistent Claims and Participations.

Append-only, in the exact spirit of the Glass Box (ledger.py): there is no
update, no delete, no rewrite in this module, on purpose. A Claim is the
durable identity of an idea; a Participation is one immutable action a seat
took in relation to a Claim. References are typed edges carried BY a
participation — never separate stored objects.

Retries are NOT a participation type. A retry is an ordinary
`type="assert"` participation on the SAME claim, carrying exactly one
`retry_of` reference to the ORIGINAL participation. That edge is the single
source of truth for retry identity — there is deliberately no `type:retry`.

    THE ONE DISTINCTION THIS MODULE LIVES OR DIES BY
    `retry_of` (a reference edge) is the graph's truth about a retry.
    `declared_resend_of` (a field) is only a NON-SEMANTIC *claim* that a
    resend happened — a caller/delivery signal, not an edge. They are
    allowed to DISAGREE, and that disagreement is the whole point: a
    declared resend with no matching edge is the detectable "unmarked
    retry." Do NOT "simplify" by auto-deriving the edge from the
    declaration — that erases the very thing Layer 1 exists to surface.

This module never validates references at write time (accept-first). A
dangling, self-referential, or wrong-type reference is accepted and
preserved exactly as written; its invalidity is a *derived* fact
(Layer 1, reasoning_observations.py), never a write-time rejection.
Corrections are new participations, never edits to history.

Storage: two append-only JSONL files under memory/, plus a Glass Box
ledger event per append so the reasoning graph inherits the room's
tamper-evident hash chain. Tests re-point CLAIMS_PATH / PARTICIPATIONS_PATH
(and ledger.LEDGER_PATH) at a temp dir, the same way the other stores do.
"""

import json
import os
import uuid
from datetime import datetime, timezone
from typing import List, Optional

import ledger

# Bump when the persisted Claim/Participation record shape changes in a way
# a reader must branch on. Stamped onto every stored record so a future
# migration can tell old rows from new without guessing.
SCHEMA_VERSION = 1

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STORE_DIR = os.path.join(BASE_DIR, "memory")
CLAIMS_PATH = os.path.join(STORE_DIR, "reasoning_claims.jsonl")
PARTICIPATIONS_PATH = os.path.join(STORE_DIR, "reasoning_participations.jsonl")

# A structurally-declared resend whose original could not be resolved from
# non-semantic signal. Preserved so the gap stays detectable — never guessed.
UNRESOLVED_TARGET = "__unresolved__"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _append_jsonl(path: str, obj: dict) -> None:
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")


def _read_jsonl(path: str) -> List[dict]:
    out = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    # A corrupt line is itself evidence — keep it visible.
                    out.append({"_corrupt": True, "raw": line[:200]})
    except OSError:
        return []
    return out


def retry_of_reference(target_participation_id: str) -> dict:
    """Construct a retry_of reference edge — the one source of retry identity.

    Returns the edge dict; it is the caller's job to attach it to a
    participation's `references`. Named `..._reference` so it reads as a
    constructor, not a predicate.
    """
    return {"type": "retry_of", "target_type": "participation",
            "target_id": target_participation_id}


def open_claim(seat: str, content: str) -> tuple:
    """Create a new Claim and its first (assert) Participation, atomically.

    A Claim is not owned by a seat; it only records which participation
    created it. Returns (claim, participation).
    """
    claim_id = _new_id("claim")
    participation_id = _new_id("participation")
    ts = _now()
    claim = {
        "schema_version": SCHEMA_VERSION,
        "claim_id": claim_id,
        "created_at": ts,
        "created_by_participation_id": participation_id,
        "status": "active",
    }
    participation = {
        "schema_version": SCHEMA_VERSION,
        "participation_id": participation_id,
        "claim_id": claim_id,
        "seat": seat,
        "type": "assert",
        "content": content,
        "references": [],
        "created_at": ts,
    }
    _append_jsonl(CLAIMS_PATH, claim)
    _append_jsonl(PARTICIPATIONS_PATH, participation)
    ledger.append(seat, "claim_created", ref=claim_id,
                  detail={"participation_id": participation_id})
    ledger.append(seat, "participation_appended", ref=participation_id,
                  detail={"claim_id": claim_id, "type": "assert", "references": []})
    return claim, participation


def append_participation(claim_id: str, seat: str, content: str,
                         ptype: str = "assert",
                         references: Optional[List[dict]] = None,
                         declared_resend_of: Optional[str] = None,
                         participation_id: Optional[str] = None) -> dict:
    """Append an immutable Participation to an existing Claim.

    references: typed edges, stored VERBATIM and never validated here.
    declared_resend_of: an optional NON-SEMANTIC structural pairing signal
        (a resend/delivery id, or a caller-supplied original pointer)
        declaring this participation is a resend of a specific original. It
        does NOT imply a retry_of edge, and must never be turned into one
        automatically — the unmarked-retry check depends on this being able
        to exist while the edge is absent.
    participation_id: normally minted here. May be supplied when the id was
        assigned upstream (cross-node graphs, out-of-order / delayed
        arrival), so a reference can name a target that arrives later.
    """
    references = [dict(r) for r in (references or [])]
    pid = participation_id or _new_id("participation")
    participation = {
        "schema_version": SCHEMA_VERSION,
        "participation_id": pid,
        "claim_id": claim_id,
        "seat": seat,
        "type": ptype,
        "content": content,
        "references": references,
        "created_at": _now(),
    }
    if declared_resend_of is not None:
        participation["declared_resend_of"] = declared_resend_of
    _append_jsonl(PARTICIPATIONS_PATH, participation)
    ledger.append(seat, "participation_appended", ref=pid,
                  detail={"claim_id": claim_id, "type": ptype,
                          "references": references,
                          "declared_resend_of": declared_resend_of})
    return participation


def append_from_retry_signal(claim_id: str, seat: str, content: str,
                             is_retry: bool = False,
                             original_participation_id: Optional[str] = None) -> dict:
    """Map a structural `is_retry` signal into the graph's one source of truth.

    This is the CORRECT path. When a caller (e.g. the Workshop) declares a
    resend and can identify the original participation from non-semantic
    signal, that becomes a proper retry_of edge — a marked retry, no finding.

    If `is_retry` is declared but the original cannot be identified, the
    event is still preserved, with `declared_resend_of` set to
    UNRESOLVED_TARGET so Layer 1 can report that the required target could
    not be resolved. The target is never guessed by content similarity.
    """
    references = []
    declared_resend_of = None
    if is_retry:
        if original_participation_id:
            references.append(retry_of_reference(original_participation_id))
        else:
            declared_resend_of = UNRESOLVED_TARGET
    return append_participation(claim_id, seat, content, references=references,
                                declared_resend_of=declared_resend_of)


# --- read-only accessors (no mutation exists in this module, by design) ---

def list_claims() -> List[dict]:
    return _read_jsonl(CLAIMS_PATH)


def list_participations() -> List[dict]:
    return _read_jsonl(PARTICIPATIONS_PATH)


def get_claim(claim_id: str) -> Optional[dict]:
    for c in _read_jsonl(CLAIMS_PATH):
        if c.get("claim_id") == claim_id:
            return c
    return None


def get_participation(participation_id: str) -> Optional[dict]:
    for p in _read_jsonl(PARTICIPATIONS_PATH):
        if p.get("participation_id") == participation_id:
            return p
    return None
