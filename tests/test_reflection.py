"""Regression suite for the FLINT Cognitive Reflection Layer v1.

Standalone: `python tests/test_reflection.py` (run with the project venv — it
imports app.py for the send-path tests). Covers the three passes, the engine,
the append-only store, feature flags / shadow-mode send-path behavior, and the
two motivating forensic cases.
"""

import copy
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import reflection
from reflection import engine, flags, hook, store
from reflection.models import ReflectionContext
from reflection.passes.mirror import MirrorPass
from reflection.passes.assumption import AssumptionPass
from reflection.passes.confidence import ConfidencePass
from reflection.passes.base import ReflectionPass


def _ctx(draft, **kw):
    return ReflectionContext(author=kw.pop("author", "FLINT"), draft_text=draft, **kw)


def _cats(result_or_warnings):
    ws = getattr(result_or_warnings, "warnings", result_or_warnings)
    return {w.category for w in ws}


# ============================ Mirror Pass =================================

def test_mirror_direct_contradiction_red():
    prior = [{"id": "P418", "text": "There is no evidence of a pipeline leak."}]
    r = engine.reflect(_ctx("The pipeline definitely leaked Claude's prompt.",
                            author="Claude", prior_participations=prior))
    w = next(w for w in r.warnings if w.category == "prior_contradiction")
    assert w.severity == "red" and w.source_reference == "P418"
    assert r.overall_severity == "red"


def test_mirror_non_contradiction_preserved():
    prior = [{"id": "P1", "text": "The garden needs watering in the morning."}]
    ws = MirrorPass().evaluate(_ctx("Let's discuss the attribution registry design.",
                                    prior_participations=prior))
    assert "prior_contradiction" not in {w.category for w in ws}


def test_mirror_repeated_unresolved_yellow():
    prior = [{"id": f"P{i}", "text": "We should treat credit discipline as a principle."}
             for i in range(3)]
    ws = MirrorPass().evaluate(_ctx("I still think credit discipline should be a principle.",
                                    prior_participations=prior))
    w = next(w for w in ws if w.category == "repeated_unresolved_claim")
    assert w.severity == "yellow" and w.metadata["occurrences"] == 3


def test_mirror_prior_correction_detected():
    receipts = [{"id": "R7", "text": "claim that seat isolation failed",
                 "resolution": "false"}]
    ws = MirrorPass().evaluate(_ctx("Seat isolation failed in that session.",
                                    receipts=receipts))
    assert "prior_correction" in {w.category for w in ws}


def test_mirror_attribution_mismatch_red_only_with_map():
    draft = "Chris, you actually ran the query against the derived view."
    ws = MirrorPass().evaluate(_ctx(draft, attribution_map={"ran the query": "Gemini"}))
    w = next(w for w in ws if w.category == "attribution_mismatch")
    assert w.severity == "red" and w.source_reference == "Gemini"
    # no map -> no attribution warning (never guessed from prose)
    ws2 = MirrorPass().evaluate(_ctx(draft))
    assert "attribution_mismatch" not in {w.category for w in ws2}


def test_mirror_quoted_text_not_counted_as_own_claim():
    # The author QUOTES a negation as evidence; must not read as the author negating.
    prior = [{"id": "P1", "text": "The detector definitely fires on the mismatch."}]
    draft = 'Claude said "there is no evidence the detector fires" — but I disagree, it fires.'
    ws = MirrorPass().evaluate(_ctx(draft, prior_participations=prior))
    assert "prior_contradiction" not in {w.category for w in ws}


def test_mirror_uncertainty_is_not_a_false_contradiction():
    prior = [{"id": "P1", "text": "The pipeline leaked the prompt."}]
    ws = MirrorPass().evaluate(_ctx("Maybe the pipeline possibly did not leak the prompt.",
                                    prior_participations=prior))
    assert "prior_contradiction" not in {w.category for w in ws}   # draft is hedged


# ========================== Assumption Pass ==============================

def test_assumption_unsupported_causal_detected():
    ws = AssumptionPass().evaluate(_ctx("The scaffolding proves the pipeline leaked the prompt."))
    assert {w.category for w in ws} & {"unsupported_causal_claim", "unsupported_assumption"}


def test_assumption_observation_vs_conclusion():
    # observation supported by a receipt; the causal leap is not.
    receipts = [{"id": "R1", "text": "scaffolding appeared in FLINT output"}]
    # pure observation -> no causal marker -> no warning
    assert AssumptionPass().evaluate(_ctx("Scaffolding appeared in FLINT's output.",
                                          receipts=receipts)) == []
    # causal conclusion on a DIFFERENT, unsupported topic -> warns
    ws = AssumptionPass().evaluate(
        _ctx("The scaffolding came from Claude's prompt because upstream fed it.",
             receipts=receipts))
    assert ws  # the causal 'because ... prompt' has no receipt


def test_assumption_supported_claim_does_not_warn():
    receipts = [{"id": "R1", "text": "the cause is a dropped retry_of edge, confirmed"}]
    assert AssumptionPass().evaluate(
        _ctx("The cause is a dropped retry_of edge.", receipts=receipts)) == []


def test_assumption_ordinary_opinion_and_caution_do_not_warn():
    assert AssumptionPass().evaluate(_ctx("I think the registry design reads cleanly.")) == []
    assert AssumptionPass().evaluate(_ctx("It might possibly help to add a test here.")) == []


def test_assumption_competing_evidence_elevates_to_red():
    receipts = [{"id": "R9", "text": "forensic scan found no cross-seat prompt exposure"}]
    ws = AssumptionPass().evaluate(
        _ctx("This proves the pipeline leaked cross-seat prompt exposure.", receipts=receipts))
    assert any(w.severity == "red" for w in ws)


def test_assumption_no_evidence_input_no_fabrication():
    ws = AssumptionPass().evaluate(_ctx("The cause is an upstream bug."))
    # a warning is fine, but it must not invent a competing explanation
    for w in ws:
        assert w.prior_excerpt == "" and w.source_reference is None


# =========================== Confidence Pass =============================

def test_confidence_unsupported_certainty_warns():
    ws = ConfidencePass().evaluate(_ctx("This definitely proves a pipeline leak.",
                                        receipts=[]))
    assert "confidence_exceeds_evidence" in {w.category for w in ws}


def test_confidence_cautious_wording_passes():
    assert ConfidencePass().evaluate(_ctx("A pipeline leak seems possible, perhaps.")) == []


def test_confidence_drift_without_new_evidence_warns():
    prior = [{"id": "P1", "text": "A pipeline leak is possible."}]
    ws = ConfidencePass().evaluate(_ctx("The pipeline definitely leaked.",
                                        prior_participations=prior))
    assert "confidence_drift" in {w.category for w in ws}


def test_confidence_increase_with_new_receipt_does_not_warn():
    prior = [{"id": "P1", "text": "A pipeline leak is possible."}]
    receipts = [{"id": "R1", "text": "pipeline leak confirmed by forensic receipt"}]
    ws = ConfidencePass().evaluate(_ctx("The pipeline definitely leaked.",
                                        prior_participations=prior, receipts=receipts))
    assert ws == []   # a receipt now supports the certainty


def test_confidence_quoted_certainty_not_authors():
    ws = ConfidencePass().evaluate(_ctx('Claude wrote "this definitely proves it" in round 26.'))
    assert ws == []


def test_confidence_no_fake_probabilities():
    ws = ConfidencePass().evaluate(_ctx("This definitely proves it.", receipts=[]))
    for w in ws:
        assert 0.0 <= w.detector_confidence <= 1.0   # heuristic match, not epistemic


# ============================== Engine ===================================

class _BoomPass(ReflectionPass):
    name = "boom"
    def evaluate(self, ctx):
        raise RuntimeError("intentional")


def test_engine_one_failed_pass_does_not_stop_others():
    passes = (_BoomPass(), ConfidencePass())
    r = engine.reflect(_ctx("This definitely proves it.", receipts=[]), passes=passes)
    boom = next(p for p in r.pass_results if p.pass_name == "boom")
    conf = next(p for p in r.pass_results if p.pass_name == "confidence")
    assert boom.ok is False and boom.error
    assert conf.ok is True and conf.warnings   # the other pass still produced output


def test_engine_deterministic_pass_order():
    c = _ctx("ordinary text about gardening")
    a = [p.pass_name for p in engine.reflect(c).pass_results]
    b = [p.pass_name for p in engine.reflect(c).pass_results]
    assert a == b == ["mirror", "assumption", "confidence"]


def test_engine_green_and_malformed_input_safe():
    assert engine.reflect(_ctx("")).overall_severity == "green"
    # malformed prior entries must not crash the engine
    r = engine.reflect(_ctx("hello there", prior_participations=[{"nope": 1}, "raw string"]))
    assert r.overall_severity in ("green", "yellow", "red")


# =============================== Store ===================================

def _tmp_store():
    d = tempfile.mkdtemp()
    store.STORE_DIR = d
    store.REFLECTIONS_PATH = os.path.join(d, "reflections.jsonl")
    return d


def test_store_append_only_valid_jsonl():
    d = _tmp_store()
    try:
        for i in range(3):
            store.record(engine.reflect(_ctx("This definitely proves it.", receipts=[])))
        lines = open(store.REFLECTIONS_PATH, encoding="utf-8").read().splitlines()
        assert len(lines) == 3 and all(json.loads(x) for x in lines)
        assert not hasattr(store, "update") and not hasattr(store, "delete")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_store_tolerates_malformed_line_and_analytics():
    d = _tmp_store()
    try:
        with open(store.REFLECTIONS_PATH, "w", encoding="utf-8") as f:
            f.write("{ this is not json\n")
        store.record(engine.reflect(_ctx("The pipeline definitely leaked the prompt.",
                                         prior_participations=[{"id": "P1", "text": "no leak here at all"}])))
        rows = store.list_reflections()
        assert len(rows) == 1          # corrupt line skipped, append still worked
        a = store.analytics()
        assert a["total_reflections"] == 1 and "warnings_by_category" in a
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_store_failure_does_not_propagate():
    d = _tmp_store()
    try:
        store.REFLECTIONS_PATH = d   # a directory, not a file -> open() fails
        assert store.record(engine.reflect(_ctx("x"))) is None   # no raise
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ===================== Flags + send-path (shadow) ========================

def _clear_flags():
    for k in ("REFLECTION_LAYER_ENABLED", "REFLECTION_LAYER_SHADOW_MODE"):
        os.environ.pop(k, None)


def test_flags_default_off_and_shadow_default_on():
    _clear_flags()
    assert flags.enabled() is False
    assert flags.shadow_mode() is True
    assert flags.enabled({"reflection_layer_enabled": True}) is True


def test_hook_off_never_calls_engine_and_is_byte_identical():
    _clear_flags()
    d = _tmp_store()
    calls = {"n": 0}
    real = engine.reflect
    reflection.engine.reflect = lambda *a, **k: (calls.__setitem__("n", calls["n"] + 1) or real(*a, **k))
    try:
        session = {"id": "s1", "rounds": [{"round": 1, "responses": [{"name": "FLINT", "text": "prior"}]}]}
        round_data = {"round": 2, "responses": [{"name": "FLINT", "text": "This definitely proves it."}]}
        before = copy.deepcopy(round_data)
        n = hook.reflect_round(session, round_data, settings=None)
        assert n == 0 and calls["n"] == 0           # engine never invoked when OFF
        assert round_data == before                  # response byte-identical
        assert not os.path.exists(store.REFLECTIONS_PATH)   # nothing stored
    finally:
        reflection.engine.reflect = real
        shutil.rmtree(d, ignore_errors=True)


def test_hook_shadow_on_stores_but_response_byte_identical():
    _clear_flags()
    os.environ["REFLECTION_LAYER_ENABLED"] = "true"   # shadow default on
    d = _tmp_store()
    try:
        session = {"id": "s2",
                   "rounds": [{"round": 1, "responses": [{"name": "Claude", "text": "A leak is possible."}]}]}
        round_data = {"round": 2,
                      "responses": [{"name": "Claude", "text": "The pipeline definitely leaked."}]}
        before = copy.deepcopy(round_data)
        session_before = copy.deepcopy(session)
        n = hook.reflect_round(session, round_data, settings=None)
        assert n == 1                                # a reflection was stored
        assert round_data == before                  # response text unchanged
        assert session == session_before             # session unchanged (hook doesn't append)
        rows = store.list_reflections()
        assert rows and rows[0]["shadow_mode"] is True and rows[0]["author"] == "Claude"
    finally:
        _clear_flags()
        shutil.rmtree(d, ignore_errors=True)


def test_hook_reflection_failure_does_not_affect_response():
    _clear_flags()
    os.environ["REFLECTION_LAYER_ENABLED"] = "true"
    d = _tmp_store()
    real = engine.reflect
    reflection.engine.reflect = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        round_data = {"round": 1, "responses": [{"name": "FLINT", "text": "hello"}]}
        before = copy.deepcopy(round_data)
        n = hook.reflect_round({"id": "s", "rounds": []}, round_data, settings=None)
        assert n == 0 and round_data == before       # failure swallowed, response intact
    finally:
        reflection.engine.reflect = real
        _clear_flags()
        shutil.rmtree(d, ignore_errors=True)


# ===================== Visible mode (reflection card) ====================

def test_flags_visible_default_off():
    _clear_flags()
    assert flags.visible() is False
    assert flags.visible({"reflection_layer_visible": True}) is True


def test_hook_visible_attaches_card_without_changing_text():
    _clear_flags()
    os.environ["REFLECTION_LAYER_ENABLED"] = "true"
    os.environ["REFLECTION_LAYER_VISIBLE"] = "true"
    d = _tmp_store()
    try:
        session = {"id": "sv",
                   "rounds": [{"round": 1, "responses": [{"name": "Claude", "text": "A leak is possible."}]}]}
        original = "The pipeline definitely leaked."
        round_data = {"round": 2, "responses": [{"name": "Claude", "id": "claude", "text": original}]}
        hook.reflect_round(session, round_data, settings=None)
        resp = round_data["responses"][0]
        assert resp["text"] == original                       # TEXT byte-identical
        assert "reflection" in resp                            # card attached
        card = resp["reflection"]
        assert set(card["passes"]) == {"mirror", "assumption", "confidence"}
        assert card["overall_severity"] in ("green", "yellow", "red")
    finally:
        _clear_flags()
        shutil.rmtree(d, ignore_errors=True)


def test_hook_shadow_does_not_attach_card():
    _clear_flags()
    os.environ["REFLECTION_LAYER_ENABLED"] = "true"   # shadow default on, visible off
    d = _tmp_store()
    try:
        round_data = {"round": 1, "responses": [{"name": "Claude", "text": "This definitely proves it."}]}
        hook.reflect_round({"id": "s", "rounds": []}, round_data, settings=None)
        assert "reflection" not in round_data["responses"][0]  # no card in shadow mode
    finally:
        _clear_flags()
        shutil.rmtree(d, ignore_errors=True)


# ======================= Motivating forensic cases =======================

def test_forensic_claude_leak_accusation():
    prior = [{"id": "P10", "text": "There is no evidence of a pipeline leak."}]
    receipts = [{"id": "R1", "text": "forensic scan: no cross-seat prompt exposure found"}]
    r = engine.reflect(_ctx(
        "This definitely proves FLINT received Claude's orchestration prompt because the pipeline leaked.",
        author="Claude", prior_participations=prior, receipts=receipts))
    cats = _cats(r)
    assert cats & {"unsupported_causal_claim", "unsupported_assumption"}   # Assumption
    assert "confidence_exceeds_evidence" in cats or "confidence_drift" in cats  # Confidence
    assert "prior_contradiction" in cats                                   # Mirror
    assert r.overall_severity == "red"


def test_forensic_gemini_attribution():
    draft = "Chris, I'm impressed you actually ran the query against the derived view."
    r = engine.reflect(_ctx(draft, author="FLINT",
                            attribution_map={"ran the query": "Gemini"}))
    assert "attribution_mismatch" in _cats(r) and r.overall_severity == "red"
    r2 = engine.reflect(_ctx(draft, author="FLINT"))       # no map -> no red mismatch
    assert "attribution_mismatch" not in _cats(r2)


ALL_TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]

if __name__ == "__main__":
    for t in ALL_TESTS:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\nALL PASS ({len(ALL_TESTS)} tests)")
