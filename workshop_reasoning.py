"""Integration seam: Workshop rounds -> reasoning-ledger Claims/Participations.

Pure wiring. This module adds NO new reasoning behavior — it only
translates Workshop events into existing reasoning_store / reasoning_
observations calls, using the version chain's own non-semantic signals.

The mapping:
  * a Workshop target (the shared artifact/goal) is one Claim;
  * each seat's content-bearing bench turn (landed / pending / rejected)
    is one `assert` Participation on that Claim;
  * a seat's successive turns on the same Claim are modeled as retries —
    each new turn carries a `retry_of` edge to that seat's most recent
    prior participation on the Claim. This is a STRUCTURAL relationship
    (same seat, same Claim, later in time); no content is inspected and
    nothing here decides whether a turn "really" is a retry. That
    interpretation is Layer 2, out of scope.

Nothing is validated at emission time. Whether the resulting graph is
well-formed is a Layer-1 question answered by observations(), never a
write-time gate.
"""

import reasoning_observations
import reasoning_store

# Content-bearing turn actions: these put a real contribution on the
# artifact chain and so become Participations. Skipped / locked / pass /
# malformed / error turns produce no artifact content and no Participation.
RECORDED_ACTIONS = {"landed", "pending", "rejected"}

# The founding participation of a target's Claim is attributed to the human
# who opened it; the AI seats then participate in the same Claim.
OPENER_SEAT = "Chris"


def open_target_claim(goal: str) -> str:
    """Open the Claim for a newly-set Workshop target. Returns claim_id.

    The target's goal is the founding assertion; the AI seats append their
    bench turns to this same Claim across cycles.
    """
    claim, _participation = reasoning_store.open_claim(OPENER_SEAT, goal)
    return claim["claim_id"]


def _last_participation_id(claim_id: str, seat: str):
    """The seat's most recent prior participation on this Claim, or None.

    Structural only: filters by claim_id and seat, in append order. No
    content is examined.
    """
    prior = [p for p in reasoning_store.list_participations()
             if not p.get("_corrupt")
             and p.get("claim_id") == claim_id
             and p.get("seat") == seat]
    return prior[-1]["participation_id"] if prior else None


def record_turn(claim_id: str, seat: str, action: str, note: str) -> dict:
    """Emit one Participation for a seat's content-bearing bench turn.

    A seat's first turn on a Claim is a plain assert; a later turn is a
    retry of that seat's most recent prior participation (mapped to a
    retry_of edge via the store's is_retry seam). Returns the Participation.
    """
    content = f"[{action}] {note}".strip()
    original = _last_participation_id(claim_id, seat)
    if original:
        return reasoning_store.append_from_retry_signal(
            claim_id, seat, content, is_retry=True,
            original_participation_id=original)
    return reasoning_store.append_participation(claim_id, seat, content)


def record_cycle(claim_id: str, turns: list) -> list:
    """Emit Participations for every content-bearing turn in a cycle report.

    Skips turns with no claim_id (a cycle that ran before a Claim was
    opened) by requiring the caller to pass one. Returns the list of
    Participations created, in turn order.
    """
    out = []
    if not claim_id:
        return out
    for t in turns:
        if t.get("action") in RECORDED_ACTIONS:
            out.append(record_turn(claim_id, t.get("seat") or t.get("name") or "unknown",
                                   t["action"], t.get("note", "")))
    return out


def graph(claim_id=None) -> list:
    """The Participations in the graph, optionally scoped to one Claim."""
    parts = reasoning_store.list_participations()
    if claim_id:
        parts = [p for p in parts if p.get("claim_id") == claim_id]
    return parts


def observations(claim_id=None) -> list:
    """Layer-1 mechanical observations over the graph (optionally scoped)."""
    return reasoning_observations.observe(graph(claim_id))


def snapshot(claim_id=None) -> dict:
    """A developer-view payload: Claims, Participations, and the derived
    Layer-1 observations, plus counts. Observations are computed here on
    demand and never persisted."""
    claims = reasoning_store.list_claims()
    parts = graph(claim_id)
    obs = reasoning_observations.observe(parts)
    return {
        "scoped_claim_id": claim_id,
        "claims": claims,
        "participations": parts,
        "observations": obs,
        "counts": {
            "claims": len(claims),
            "participations": len([p for p in parts if not p.get("_corrupt")]),
            "observations": len(obs),
        },
    }
