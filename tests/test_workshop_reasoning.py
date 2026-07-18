"""End-to-end wiring test: Workshop cycle reports -> reasoning graph -> view.

Standalone (no pytest): `python tests/test_workshop_reasoning.py`. Exercises
the same code app.py calls (workshop_reasoning + the reasoning store), so
the flow is validated without needing FastAPI or any AI provider. The store
and Glass Box ledger are re-pointed at a temp dir.

The correction under test: retries are NOT inferred from same-seat/same-
Claim ordering. Normal successive turns stay unlinked; a retry_of edge
appears only when an explicit retry signal names the original.
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ledger
import reasoning_store as RS
import workshop_reasoning as WR


def _fresh():
    d = tempfile.mkdtemp()
    RS.CLAIMS_PATH = os.path.join(d, "reasoning_claims.jsonl")
    RS.PARTICIPATIONS_PATH = os.path.join(d, "reasoning_participations.jsonl")
    ledger.LEDGER_DIR = d
    ledger.LEDGER_PATH = os.path.join(d, "ledger.jsonl")
    return d


def _turns(*specs):
    return [{"seat": s, "name": s.title(), "action": a, "note": n} for s, a, n in specs]


def _all_retry_edges(parts):
    return [r for p in parts for r in p.get("references", []) if r.get("type") == "retry_of"]


def test_normal_successive_turns_create_no_retry_edges():
    """Two normal turns from the same seat on the same Claim -> two
    Participations, ZERO retry_of edges. Multiple seats share one Claim."""
    d = _fresh()
    try:
        claim_id = WR.open_target_claim("Make the parser reject empty input")

        WR.record_cycle(claim_id, _turns(
            ("claude", "landed", "add guard clause"),
            ("chatgpt", "pending", "add a test"),
            ("flint", "pass", "nothing to add"),        # pass -> no participation
        ))
        WR.record_cycle(claim_id, _turns(
            ("claude", "landed", "refine the guard clause"),   # a later, DIFFERENT turn
            ("chatgpt", "landed", "tighten the test"),
        ))

        parts = WR.graph(claim_id)
        # founding (Chris) + claude x2 + chatgpt x2 = 5; flint's pass adds none
        assert len(parts) == 5, [p["seat"] for p in parts]
        assert {p["seat"] for p in parts} == {"Chris", "claude", "chatgpt"}
        assert all(p["claim_id"] == claim_id for p in parts)
        assert len(RS.list_claims()) == 1                 # one shared Claim

        # THE CORRECTION: no retry_of edges were manufactured from ordering
        assert _all_retry_edges(parts) == []
        # and therefore a well-formed session has zero Layer-1 findings
        assert WR.observations(claim_id) == []
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_no_order_or_content_inference():
    """Even two identical-content successive turns from one seat get no
    edge — proving neither turn order nor content is used to infer retries."""
    d = _fresh()
    try:
        claim_id = WR.open_target_claim("goal")
        WR.record_cycle(claim_id, _turns(("claude", "rejected", "same note")))
        WR.record_cycle(claim_id, _turns(("claude", "landed", "same note")))
        assert _all_retry_edges(WR.graph(claim_id)) == []
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_explicit_retry_with_known_original_creates_one_edge():
    """The seam: an explicit retry signal naming the original creates
    exactly one valid retry_of edge, and no finding."""
    d = _fresh()
    try:
        claim_id = WR.open_target_claim("goal")
        first = WR.record_turn(claim_id, "claude", "rejected", "first attempt")
        retry = WR.record_turn(claim_id, "claude", "landed", "second attempt",
                               is_retry=True,
                               original_participation_id=first["participation_id"])
        edges = [r for r in retry["references"] if r["type"] == "retry_of"]
        assert len(edges) == 1
        assert edges[0]["target_id"] == first["participation_id"]
        assert WR.observations(claim_id) == []          # marked retry: silent
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_explicit_retry_without_resolvable_original_surfaces_finding():
    """The seam: an explicit retry signal that can't name the original is
    preserved and produces the existing deterministic Layer-1 finding."""
    d = _fresh()
    try:
        claim_id = WR.open_target_claim("goal")
        WR.record_turn(claim_id, "claude", "landed", "resend, no pointer",
                       is_retry=True, original_participation_id=None)
        obs = WR.observations(claim_id)
        assert len(obs) == 1
        assert obs[0]["observation_type"] == "missing_expected_reference"
        assert obs[0]["expected_target_participation_id"] is None
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_disposition_preserved_as_fact_not_in_content():
    """landed/pending/rejected is preserved as a structured meta fact; the
    content is the seat's note, not polluted with the disposition."""
    d = _fresh()
    try:
        claim_id = WR.open_target_claim("goal")
        made = WR.record_cycle(claim_id, _turns(
            ("claude", "landed", "did the thing"),
            ("chatgpt", "rejected", "broke a case"),
        ))
        assert made[0]["meta"]["workshop_disposition"] == "landed"
        assert made[0]["content"] == "did the thing"       # not "[landed] ..."
        assert made[1]["meta"]["workshop_disposition"] == "rejected"
        assert made[1]["content"] == "broke a case"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_dev_view_computes_observations_on_demand_and_never_persists():
    d = _fresh()
    try:
        claim_id = WR.open_target_claim("goal")
        WR.record_cycle(claim_id, _turns(("claude", "landed", "x")))
        snap = WR.snapshot(claim_id)
        assert snap["counts"]["claims"] == 1
        assert snap["counts"]["participations"] == 2       # founding + one turn
        assert snap["observations"] == []
        # observations are derived — no ledger event, no file on disk
        with open(ledger.LEDGER_PATH, encoding="utf-8") as f:
            assert "observation_type" not in f.read()
        assert not any("observation" in fn for fn in os.listdir(d))
    finally:
        shutil.rmtree(d, ignore_errors=True)


ALL_TESTS = [
    test_normal_successive_turns_create_no_retry_edges,
    test_no_order_or_content_inference,
    test_explicit_retry_with_known_original_creates_one_edge,
    test_explicit_retry_without_resolvable_original_surfaces_finding,
    test_disposition_preserved_as_fact_not_in_content,
    test_dev_view_computes_observations_on_demand_and_never_persists,
]


if __name__ == "__main__":
    for t in ALL_TESTS:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\nALL PASS ({len(ALL_TESTS)} tests)")
