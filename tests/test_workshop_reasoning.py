"""End-to-end wiring test: Workshop cycle reports -> reasoning graph -> view.

Standalone (no pytest): `python tests/test_workshop_reasoning.py`. Exercises
the same code app.py calls (workshop_reasoning + the reasoning store), so
the flow is validated without needing FastAPI or any AI provider. The store
and Glass Box ledger are re-pointed at a temp dir.

This proves the integration end to end:
  - setting a target opens exactly one Claim,
  - each content-bearing turn becomes an assert Participation,
  - a seat's successive turns are marked retry_of its prior,
  - multiple seats share the one Claim,
  - the dev-view snapshot exposes the graph + Layer-1 observations,
  - a well-formed session yields zero findings,
  - and a dropped edge is still surfaced (observability actually works).
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
    """Build a minimal cycle report's turns list: (seat, action, note)."""
    return [{"seat": s, "name": s.title(), "action": a, "note": n} for s, a, n in specs]


def test_end_to_end_workshop_to_reasoning_view():
    d = _fresh()
    try:
        # 1. setting a target opens exactly one Claim
        claim_id = WR.open_target_claim("Make the parser reject empty input")
        assert len(RS.list_claims()) == 1
        assert RS.list_claims()[0]["claim_id"] == claim_id

        # 2. cycle 1: two seats each land a first contribution
        made = WR.record_cycle(claim_id, _turns(
            ("claude", "landed", "add guard clause"),
            ("chatgpt", "pending", "add a test"),
            ("flint", "pass", "nothing to add"),      # pass -> no participation
        ))
        assert len(made) == 2, "only content-bearing turns become participations"

        # 3. cycle 2: claude's edit was rejected and it resubmits -> retry_of prior
        WR.record_cycle(claim_id, _turns(
            ("claude", "rejected", "guard clause broke a case"),
            ("chatgpt", "landed", "tighten the test"),
        ))
        WR.record_cycle(claim_id, _turns(
            ("claude", "landed", "fixed guard clause"),
        ))

        parts = WR.graph(claim_id)
        # founding participation (Chris) + claude x3 + chatgpt x2 = 6
        assert len(parts) == 6, [p["seat"] for p in parts]

        # 4. multiple seats share the ONE Claim
        seats = {p["seat"] for p in parts}
        assert seats == {"Chris", "claude", "chatgpt"}
        assert all(p["claim_id"] == claim_id for p in parts)
        assert len(RS.list_claims()) == 1  # no claim minted per turn

        # 5. a seat's later turns carry retry_of edges to its prior participation
        claude_parts = [p for p in parts if p["seat"] == "claude"]
        assert len(claude_parts) == 3
        assert claude_parts[0]["references"] == []          # first turn: plain assert
        for earlier, later in zip(claude_parts, claude_parts[1:]):
            edges = [r for r in later["references"] if r["type"] == "retry_of"]
            assert len(edges) == 1
            assert edges[0]["target_id"] == earlier["participation_id"]

        # 6. dev-view snapshot exposes the graph + observations, and a
        #    well-formed session has ZERO Layer-1 findings
        snap = WR.snapshot(claim_id)
        assert snap["counts"]["claims"] == 1
        assert snap["counts"]["participations"] == 6
        assert snap["observations"] == [], snap["observations"]
        assert snap["counts"]["observations"] == 0

        # 7. observations are derived, not persisted (no ledger event, no file)
        with open(ledger.LEDGER_PATH, encoding="utf-8") as f:
            assert "observation_type" not in f.read()
        assert not any("observation" in fn for fn in os.listdir(d))

        # 8. observability actually works: simulate a dropped edge (a declared
        #    resend whose retry_of never got written) and confirm it surfaces
        orig = claude_parts[-1]["participation_id"]
        RS.append_participation(claim_id, "claude", "[landed] resend, edge dropped",
                                references=[], declared_resend_of=orig)
        obs = WR.observations(claim_id)
        assert len(obs) == 1
        assert obs[0]["observation_type"] == "missing_expected_reference"
        assert obs[0]["expected_target_participation_id"] == orig
    finally:
        shutil.rmtree(d, ignore_errors=True)


ALL_TESTS = [test_end_to_end_workshop_to_reasoning_view]


if __name__ == "__main__":
    for t in ALL_TESTS:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\nALL PASS ({len(ALL_TESTS)} tests)")
