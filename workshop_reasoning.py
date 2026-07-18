"""Integration seam: Workshop rounds -> reasoning-ledger Claims/Participations.

Pure wiring. This module adds NO new reasoning behavior — it only
translates Workshop events into existing reasoning_store / reasoning_
observations calls.

The mapping:
  * a Workshop target (the shared artifact/goal) is one Claim;
  * each seat's content-bearing bench turn (landed / pending / rejected)
    is one `assert` Participation on that Claim, with the turn's
    disposition preserved as a fact in `meta`;
  * multiple seats participate in the same Claim.

RETRIES ARE NOT INFERRED FROM ORDERING.
An earlier version of this module linked a seat's successive turns with a
`retry_of` edge. That was wrong: same seat + same Claim proves only that a
turn came later — it may be a refinement, a rebuttal, new evidence, a
correction, or a genuinely new contribution. Auto-linking manufactures
false edges and destroys the meaning of retry_of.

The Workshop execution path carries no genuine, non-semantic retry signal
today: a bench turn is only a PASS or an EDIT (a fence + note), and the
version chain's prev_hash links chronologically, not to a specific prior
attempt. So live retry emission is NOT wired — normal turns are left
unlinked. The seam below is ready for the day a real signal exists (an
explicit retry flag, a resend/delivery id, or a caller-supplied original
participation id): pass it through `record_turn`, and only then is a
`retry_of` edge created — via the store's `append_from_retry_signal`, the
one source of truth. Nothing here ever guesses an original from content or
turn order.
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


def record_turn(claim_id: str, seat: str, action: str, note: str,
                is_retry: bool = False,
                original_participation_id=None) -> dict:
    """Emit one Participation for a seat's content-bearing bench turn.

    By default a turn is a plain `assert` Participation with NO retry_of
    edge — successive turns by a seat are just separate Participations on
    the same Claim. The turn's disposition (landed / pending / rejected) is
    recorded as a fact in `meta`, not baked into the content.

    THE RETRY SEAM (not exercised by live cycles yet): a caller that has a
    genuine, non-semantic retry signal passes `is_retry=True`. Only then is
    a retry_of edge created, and only through the store's
    `append_from_retry_signal`:
      * with a resolvable `original_participation_id` -> one valid retry_of;
      * without one -> the event is preserved and Layer 1 reports the
        original could not be resolved. The original is never guessed.
    """
    meta = {"workshop_disposition": action}
    content = note or ""
    if is_retry:
        return reasoning_store.append_from_retry_signal(
            claim_id, seat, content, is_retry=True,
            original_participation_id=original_participation_id, meta=meta)
    return reasoning_store.append_participation(claim_id, seat, content, meta=meta)


def record_cycle(claim_id: str, turns: list) -> list:
    """Emit Participations for every content-bearing turn in a cycle report.

    Live cycles carry no retry signal, so every turn here is recorded as a
    plain Participation with no retry_of edge. Returns the Participations
    created, in turn order.
    """
    out = []
    if not claim_id:
        return out
    for t in turns:
        if t.get("action") in RECORDED_ACTIONS:
            out.append(record_turn(claim_id,
                                   t.get("seat") or t.get("name") or "unknown",
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
