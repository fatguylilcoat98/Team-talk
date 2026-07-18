"""Acceptance + focused tests for the reasoning-ledger vertical slice.

Standalone (no pytest): `python tests/test_reasoning_ledger.py`. The store
and the Glass Box ledger are re-pointed at a throwaway temp dir so nothing
touches real room data. Failures raise AssertionError; a clean run prints
ALL PASS.

The essential receipt: the derived observation fires on the planted
unmarked retry and stays silent on the correctly marked retry.
"""

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ledger
import reference_registry as REG
import reasoning_store as RS
import reasoning_observations as OBS


def _fresh():
    d = tempfile.mkdtemp()
    RS.CLAIMS_PATH = os.path.join(d, "reasoning_claims.jsonl")
    RS.PARTICIPATIONS_PATH = os.path.join(d, "reasoning_participations.jsonl")
    ledger.LEDGER_DIR = d
    ledger.LEDGER_PATH = os.path.join(d, "ledger.jsonl")
    return d


def _retry_findings(obs, src=None):
    kinds = {"dangling_reference", "missing_expected_reference", "invalid_reference"}
    fs = [o for o in obs
          if o.get("reference_type") == "retry_of" and o["observation_type"] in kinds]
    if src is not None:
        fs = [o for o in fs if o["source_participation_id"] == src]
    return fs


# --------------------------------------------------------------------------
# The required 12-step acceptance test
# --------------------------------------------------------------------------

def test_acceptance():
    d = _fresh()
    try:
        # 1. one original Claim + original Participation
        claim, orig = RS.open_claim("flint", "Original assertion")
        assert claim["status"] == "active"
        assert orig["type"] == "assert" and orig["claim_id"] == claim["claim_id"]

        # 2. one correctly marked retry: same claim, assert, one retry_of edge
        marked = RS.append_participation(
            claim["claim_id"], "flint", "Resent assertion",
            references=[RS.retry_of_reference(orig["participation_id"])])
        assert marked["type"] == "assert"
        assert marked["claim_id"] == claim["claim_id"]
        assert len(marked["references"]) == 1
        assert marked["references"][0]["target_id"] == orig["participation_id"]

        # 3. one structurally-identified retry that LACKS the retry_of edge
        unmarked = RS.append_participation(
            claim["claim_id"], "flint", "Resent, edge dropped",
            references=[], declared_resend_of=orig["participation_id"])

        # 4. run the Layer-1 derived observation query
        obs = OBS.observe(RS.list_participations())

        # 5. marked retry produces NO retry-related finding
        assert _retry_findings(obs, marked["participation_id"]) == [], \
            "marked retry must be silent"

        # 6. unmarked retry produces EXACTLY ONE missing_expected_reference
        um = _retry_findings(obs, unmarked["participation_id"])
        assert len(um) == 1, f"expected exactly one finding, got {um}"
        assert um[0]["observation_type"] == "missing_expected_reference"

        # 7. the finding identifies the emitting Participation (and its target)
        assert um[0]["source_participation_id"] == unmarked["participation_id"]
        assert um[0]["expected_target_participation_id"] == orig["participation_id"]

        # 8. no Layer-2 interpretation required — only a versioned mechanical fact
        assert um[0]["registry_version"] == REG.version()
        blob = json.dumps(obs).lower()
        for banned in ("intent", "dishonest", "conceal", "semantic", "same idea"):
            assert banned not in blob, f"Layer-1 leaked a Layer-2 word: {banned}"

        # 9. observations are NOT written into the event ledger
        with open(ledger.LEDGER_PATH, encoding="utf-8") as f:
            led = f.read()
        assert "observation_type" not in led, "observations must never hit the ledger"
        assert "participation_appended" in led, "Layer-0 appends ARE ledgered"

        # 10. deterministic — recompute, identical result
        assert OBS.observe(RS.list_participations()) == obs

        # 11. a dangling retry_of target -> dangling_reference observation
        dangling = RS.append_participation(
            claim["claim_id"], "flint", "retry of a ghost",
            references=[RS.retry_of_reference("participation_does_not_exist")])
        obs2 = OBS.observe(RS.list_participations())
        dfs = _retry_findings(obs2, dangling["participation_id"])
        assert len(dfs) == 1 and dfs[0]["observation_type"] == "dangling_reference"
        assert dfs[0]["mechanical_fact"] == "target_not_found"
        assert dfs[0]["target_participation_id"] == "participation_does_not_exist"

        # 12. delayed arrival: the missing target later shows up. Recompute and
        #     the dangling finding disappears — WITHOUT editing any history.
        target_id = "participation_delayed_arrival"
        ref_p = RS.append_participation(
            claim["claim_id"], "flint", "retry of a not-yet-arrived original",
            references=[RS.retry_of_reference(target_id)])
        before = OBS.observe(RS.list_participations())
        assert any(o["observation_type"] == "dangling_reference"
                   and o["source_participation_id"] == ref_p["participation_id"]
                   for o in before)
        snapshot = RS.get_participation(ref_p["participation_id"])
        RS.append_participation(claim["claim_id"], "flint", "the awaited original",
                                participation_id=target_id)
        after = OBS.observe(RS.list_participations())
        assert not any(o["observation_type"] == "dangling_reference"
                       and o["source_participation_id"] == ref_p["participation_id"]
                       for o in after), "dangling should clear on delayed arrival"
        # the referencing participation was not mutated
        assert RS.get_participation(ref_p["participation_id"]) == snapshot
    finally:
        shutil.rmtree(d, ignore_errors=True)


# --------------------------------------------------------------------------
# Additional focused tests
# --------------------------------------------------------------------------

def test_retry_must_point_to_participation_not_claim():
    d = _fresh()
    try:
        claim, orig = RS.open_claim("flint", "orig")
        # pointing a retry_of at the CLAIM id (not a participation) -> dangling
        wrong = RS.append_participation(claim["claim_id"], "flint", "retry",
                                        references=[RS.retry_of_reference(claim["claim_id"])])
        f = _retry_findings(OBS.observe(RS.list_participations()), wrong["participation_id"])
        assert len(f) == 1 and f[0]["mechanical_fact"] == "target_not_found"
        # pointing at the participation id -> silent
        right = RS.append_participation(claim["claim_id"], "flint", "retry",
                                        references=[RS.retry_of_reference(orig["participation_id"])])
        assert _retry_findings(OBS.observe(RS.list_participations()),
                               right["participation_id"]) == []
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_max_cardinality_one():
    d = _fresh()
    try:
        claim, a = RS.open_claim("flint", "a")
        _, b = RS.open_claim("flint", "b")  # a second real participation to point at
        two = RS.append_participation(
            claim["claim_id"], "flint", "double retry",
            references=[RS.retry_of_reference(a["participation_id"]),
                        RS.retry_of_reference(b["participation_id"])])
        f = _retry_findings(OBS.observe(RS.list_participations()), two["participation_id"])
        card = [o for o in f if o.get("mechanical_fact") == "cardinality_exceeded"]
        assert len(card) == 1 and card[0]["count"] == 2
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_self_reference():
    d = _fresh()
    try:
        claim, _ = RS.open_claim("flint", "root")
        selfie = "participation_selfie"
        p = RS.append_participation(claim["claim_id"], "flint", "I retry myself",
                                    references=[RS.retry_of_reference(selfie)],
                                    participation_id=selfie)
        f = _retry_findings(OBS.observe(RS.list_participations()), p["participation_id"])
        assert len(f) == 1 and f[0]["mechanical_fact"] == "target_is_self"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_wrong_target_type():
    d = _fresh()
    try:
        claim, orig = RS.open_claim("flint", "orig")
        # target_id resolves to a real participation, but target_type is wrong
        bad = RS.append_participation(
            claim["claim_id"], "flint", "wrong type edge",
            references=[{"type": "retry_of", "target_type": "claim",
                         "target_id": orig["participation_id"]}])
        f = _retry_findings(OBS.observe(RS.list_participations()), bad["participation_id"])
        assert len(f) == 1 and f[0]["mechanical_fact"] == "wrong_target_type"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_missing_target_detected():
    d = _fresh()
    try:
        claim, _ = RS.open_claim("flint", "orig")
        p = RS.append_participation(claim["claim_id"], "flint", "ghost",
                                    references=[RS.retry_of_reference("participation_nope")])
        f = _retry_findings(OBS.observe(RS.list_participations()), p["participation_id"])
        assert len(f) == 1 and f[0]["mechanical_fact"] == "target_not_found"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_registry_version_in_results():
    d = _fresh()
    try:
        claim, _ = RS.open_claim("flint", "orig")
        RS.append_participation(claim["claim_id"], "flint", "x",
                                references=[RS.retry_of_reference("participation_nope")])
        obs = OBS.observe(RS.list_participations())
        assert obs and all(o["registry_version"] == REG.version() for o in obs)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_history_is_immutable():
    d = _fresh()
    try:
        claim, orig = RS.open_claim("flint", "orig")
        snap = RS.get_participation(orig["participation_id"])
        # a correction is a NEW event, never an edit
        RS.append_participation(claim["claim_id"], "flint", "correction",
                                references=[RS.retry_of_reference(orig["participation_id"])])
        assert RS.get_participation(orig["participation_id"]) == snap
        # the module offers no mutation surface at all
        assert not hasattr(RS, "update_participation")
        assert not hasattr(RS, "delete_participation")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_retry_does_not_duplicate_claim():
    d = _fresh()
    try:
        claim, orig = RS.open_claim("flint", "orig")
        RS.append_participation(claim["claim_id"], "flint", "retry",
                                references=[RS.retry_of_reference(orig["participation_id"])])
        assert len(RS.list_claims()) == 1
        assert RS.list_claims()[0]["claim_id"] == claim["claim_id"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_observations_recomputed_not_persisted():
    d = _fresh()
    try:
        claim, _ = RS.open_claim("flint", "orig")
        RS.append_participation(claim["claim_id"], "flint", "x",
                                references=[RS.retry_of_reference("participation_nope")])
        OBS.observe(RS.list_participations())
        # no observations file was created anywhere in the store dir
        files = os.listdir(d)
        assert not any("observation" in f for f in files), files
        # and the ledger holds no observation events
        with open(ledger.LEDGER_PATH, encoding="utf-8") as f:
            assert "observation_type" not in f.read()
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_never_claims_intent():
    d = _fresh()
    try:
        claim, orig = RS.open_claim("flint", "orig")
        RS.append_participation(claim["claim_id"], "flint", "unmarked",
                                references=[], declared_resend_of=orig["participation_id"])
        blob = json.dumps(OBS.observe(RS.list_participations())).lower()
        for banned in ("intent", "dishonest", "conceal", "accidental",
                       "deliberate", "semantic"):
            assert banned not in blob
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_multiple_seats_one_claim():
    d = _fresh()
    try:
        claim, _ = RS.open_claim("flint", "orig")
        p2 = RS.append_participation(claim["claim_id"], "chatgpt", "I join this claim")
        p3 = RS.append_participation(claim["claim_id"], "claude", "so do I")
        assert p2["claim_id"] == claim["claim_id"] == p3["claim_id"]
        assert p2["seat"] == "chatgpt" and p3["seat"] == "claude"
        assert len(RS.list_claims()) == 1  # still one shared idea, many seats
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_registry_checks_finite_and_declared():
    checks = REG.mechanical_checks("retry_of")
    assert checks == ["target_exists", "target_is_participation", "target_is_not_self"]
    # the validator's emitted facts are a finite, declared set
    assert OBS.MECHANICAL_FACTS == {
        "target_not_found", "wrong_target_type",
        "target_is_self", "cardinality_exceeded"}


def test_is_retry_signal_maps_to_retry_of():
    d = _fresh()
    try:
        claim, orig = RS.open_claim("flint", "orig")
        # is_retry with a resolvable original -> a proper marked retry, silent
        good = RS.append_from_retry_signal(
            claim["claim_id"], "flint", "resent", is_retry=True,
            original_participation_id=orig["participation_id"])
        assert len(good["references"]) == 1
        assert good["references"][0]["type"] == "retry_of"
        assert good["references"][0]["target_id"] == orig["participation_id"]
        assert _retry_findings(OBS.observe(RS.list_participations()),
                               good["participation_id"]) == []
        # is_retry declared but original unresolvable -> preserved, surfaced,
        # target NOT guessed
        blind = RS.append_from_retry_signal(
            claim["claim_id"], "flint", "resent, no pointer", is_retry=True)
        f = _retry_findings(OBS.observe(RS.list_participations()), blind["participation_id"])
        assert len(f) == 1 and f[0]["observation_type"] == "missing_expected_reference"
        assert f[0]["expected_target_participation_id"] is None
    finally:
        shutil.rmtree(d, ignore_errors=True)


ALL_TESTS = [
    test_acceptance,
    test_retry_must_point_to_participation_not_claim,
    test_max_cardinality_one,
    test_self_reference,
    test_wrong_target_type,
    test_missing_target_detected,
    test_registry_version_in_results,
    test_history_is_immutable,
    test_retry_does_not_duplicate_claim,
    test_observations_recomputed_not_persisted,
    test_never_claims_intent,
    test_multiple_seats_one_claim,
    test_registry_checks_finite_and_declared,
    test_is_retry_signal_maps_to_retry_of,
]


if __name__ == "__main__":
    for t in ALL_TESTS:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\nALL PASS ({len(ALL_TESTS)} tests)")
