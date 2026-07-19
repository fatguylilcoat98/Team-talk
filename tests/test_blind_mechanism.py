"""Unit tests for the blind-experiment mechanism: sealing, scoping, and the
identity guard — independent of whether app.py calls any of it.

Standalone (no pytest): `python tests/test_blind_mechanism.py`. Store paths
are re-pointed at a throwaway temp dir, the same way test_ledger_query.py
does it, so nothing touches real room data.

These are the receipts behind "25 leaks -> 0": the sealed mapping is not
derivable from the session id, a fresh experiment starts with an EMPTY scope
(so pre-existing named history is simply not shown, not leaked), residual
identity inside an in-scope round's own text fails closed, and the outbound
prompt audit catches what scoping alone would miss (mailbox/receipt/journal
boot blocks, self-descriptive leaks like "the 7B seat").
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import blind_context as BC
import blind_experiment as BE
import identity_guard as IG
import ledger

_TMP = None

PARTICIPANTS = [
    {"id": "claude", "name": "Claude", "model": "claude-opus", "provider": "anthropic"},
    {"id": "gemini", "name": "Gemini", "model": "gemini-2.5", "provider": "google"},
    {"id": "flint", "name": "FLINT", "model": "llama3.1:8b", "provider": "ollama"},
]


def _fresh():
    global _TMP
    _TMP = tempfile.mkdtemp()
    BE.STORE_DIR = _TMP
    BE.EXPERIMENTS_PATH = os.path.join(_TMP, "blind_experiments.jsonl")
    ledger.LEDGER_DIR = _TMP
    ledger.LEDGER_PATH = os.path.join(_TMP, "ledger.jsonl")


def _cleanup():
    if _TMP and os.path.isdir(_TMP):
        shutil.rmtree(_TMP, ignore_errors=True)


# ---- sealing -------------------------------------------------------------

def test_mapping_is_not_derivable_from_session_id():
    _fresh()
    exp = BE.open_experiment("sess-1", PARTICIPANTS, by="chris")
    # public_view (what a UI/model/export may see) never carries the mapping
    # before reveal — only the labels, unattributed.
    assert "mapping" not in exp
    assert exp["sealed"] is True
    assert sorted(exp["labels"]) == ["Voice 1", "Voice 2", "Voice 3"]
    # The SAME session id, opened again as a fresh experiment, must NOT
    # reproduce the same mapping — otherwise it is recomputable, not sealed.
    full = BE.get(exp["experiment_id"])
    seen = set()
    for _ in range(8):
        BE.close(exp["experiment_id"])
        e2 = BE.open_experiment("sess-1", PARTICIPANTS, by="chris")
        m2 = BE.get(e2["experiment_id"])["mapping"]
        seen.add(tuple(sorted(m2.items())))
    assert len(seen) > 1, "mapping repeated across opens — recomputable, not sealed"
    _cleanup()


def test_reveal_is_manual_and_nothing_else_flips_it():
    _fresh()
    exp = BE.open_experiment("sess-2", PARTICIPANTS)
    for _ in range(3):
        BE.record_round(exp["experiment_id"], "sess-2", 1)
    v = BE.public_view(exp["experiment_id"])
    assert v["revealed"] is False and "mapping" not in v
    revealed = BE.reveal(exp["experiment_id"], by="chris")
    assert revealed["revealed"] is True
    assert revealed["mapping"]["claude"].startswith("Voice ")
    _cleanup()


def test_compromise_is_recorded_never_silent():
    _fresh()
    exp = BE.open_experiment("sess-3", PARTICIPANTS)
    v = BE.mark_compromised(exp["experiment_id"], [{"kind": "identity_in_prose", "term": "Claude"}])
    assert v["compromised"] is True
    events = [e for e in ledger._read_all() if e.get("action") == "blind_experiment_compromised"]
    assert len(events) == 1
    _cleanup()


# ---- scoping (the actual 25-leak fix) -------------------------------------

def test_fresh_experiment_scope_is_empty_pre_existing_history_never_shown():
    _fresh()
    dirty_history = [
        {"round": 1, "chris_message": "hi", "responses": [
            {"id": "claude", "name": "Claude", "text": "I'm Claude and I think X."},
            {"id": "gemini", "name": "Gemini", "text": "Gemini here, I disagree."},
        ]},
    ]
    exp = BE.open_experiment("sess-4", PARTICIPANTS)
    full = BE.get(exp["experiment_id"])
    bt = BC.prepare_blind_turn(dirty_history, full, PARTICIPANTS)
    # Nothing is in scope yet — the fresh window starts empty, not redacted.
    assert bt["history"] == []
    assert bt["rounds_excluded"] == 1
    assert bt["safe_to_run"] is True
    _cleanup()


def test_in_scope_round_relabelled_no_real_name_in_rendered_history():
    _fresh()
    exp = BE.open_experiment("sess-5", PARTICIPANTS)
    full = BE.get(exp["experiment_id"])
    BE.record_round(exp["experiment_id"], "sess-5", 1)
    full = BE.get(exp["experiment_id"])
    history = [
        {"round": 1, "chris_message": "hi", "responses": [
            {"id": "claude", "name": "Claude", "text": "Agreed."},
            {"id": "gemini", "name": "Gemini", "text": "Also agreed."},
            {"id": "flint", "name": "FLINT", "text": "Same."},
        ]},
    ]
    bt = BC.prepare_blind_turn(history, full, PARTICIPANTS)
    assert bt["safe_to_run"] is True
    rendered = bt["history"][0]
    for resp in rendered["responses"]:
        assert resp["label"].startswith("Voice ")
        assert resp["name"].startswith("Voice ")
        assert "persona" not in resp and "color" not in resp and "model" not in resp
    for term in ("Claude", "Gemini", "FLINT"):
        assert not any(term in r["text"] for r in rendered["responses"])
    _cleanup()


def test_residual_identity_in_scoped_round_text_fails_closed():
    _fresh()
    exp = BE.open_experiment("sess-6", PARTICIPANTS)
    BE.record_round(exp["experiment_id"], "sess-6", 1)
    full = BE.get(exp["experiment_id"])
    # A dirty in-scope round: the response text itself names another seat —
    # the exact class of leak scoping alone can't fix (content, not authorship).
    history = [
        {"round": 1, "chris_message": "hi", "responses": [
            {"id": "claude", "name": "Claude", "text": "I think Gemini is wrong here."},
        ]},
    ]
    bt = BC.prepare_blind_turn(history, full, PARTICIPANTS)
    assert bt["safe_to_run"] is False
    assert any(r["term"] == "Gemini" for r in bt["residual_identity"])
    _cleanup()


def test_original_session_record_is_never_rewritten():
    _fresh()
    exp = BE.open_experiment("sess-7", PARTICIPANTS)
    BE.record_round(exp["experiment_id"], "sess-7", 1)
    full = BE.get(exp["experiment_id"])
    original_resp = {"id": "claude", "name": "Claude", "text": "hello"}
    history = [{"round": 1, "chris_message": "hi", "responses": [dict(original_resp)]}]
    BC.prepare_blind_turn(history, full, PARTICIPANTS)
    # The caller's own history list/dicts are untouched — relabel_round
    # returns copies, never mutates in place.
    assert history[0]["responses"][0] == original_resp
    _cleanup()


# ---- identity guard: outbound prompt audit --------------------------------

def test_audit_prompt_flags_another_participants_name():
    prompt = "You are Voice 2. Earlier Gemini made a strong point."
    audit = IG.audit_prompt(prompt, PARTICIPANTS, me_id="claude")
    assert not audit["clean"]
    assert any(l["term"] == "Gemini" for l in audit["leaks"])


def test_audit_prompt_allows_recipients_own_identity_terms_elsewhere():
    # me_id's OWN terms are excluded from the leak scan — only OTHERS matter.
    prompt = "You are claude-opus, running as Voice 1."
    audit = IG.audit_prompt(prompt, PARTICIPANTS, me_id="claude")
    assert audit["clean"], audit["leaks"]


def test_audit_prompt_catches_provider_and_model_leak_not_just_name():
    prompt = "The last reply came from an anthropic model, hosted elsewhere."
    audit = IG.audit_prompt(prompt, PARTICIPANTS, me_id="gemini")
    assert not audit["clean"]


def test_audit_all_prompts_gate_is_all_or_nothing():
    prompts = {
        "claude": "clean prompt, no other names",
        "gemini": "clean prompt too",
        "flint": "oops, mentions Claude by name",
    }
    r = IG.audit_all_prompts(prompts, PARTICIPANTS)
    assert r["clean"] is False
    assert r["per_seat"]["claude"]["clean"] and r["per_seat"]["gemini"]["clean"]
    assert not r["per_seat"]["flint"]["clean"]


# ---- identity guard: inbound header strip vs prose compromise -------------

def test_strip_identity_header_removes_only_the_leading_announcement():
    text = "FLINT to the room:\nHere is my real argument about caching."
    out = IG.strip_identity_header(text, identity_terms=["FLINT"])
    assert out["changed"] is True
    assert out["text"] == "Here is my real argument about caching."


def test_strip_identity_header_never_touches_mid_message_text():
    text = "My argument is that FLINT to the room is a bad pattern."
    out = IG.strip_identity_header(text, identity_terms=["FLINT"])
    assert out["changed"] is False
    assert out["text"] == text


def test_guard_response_header_removed_and_clean():
    r = IG.guard_response("Responding as FLINT:\nCaching should be bounded.",
                          me_id="flint", participants=PARTICIPANTS)
    assert r["header_removed"]
    assert r["clean"] is True
    assert "FLINT" not in r["text"]


def test_guard_response_prose_leak_is_reported_not_edited():
    original = "I think Gemini's argument about TTL is actually right."
    r = IG.guard_response(original, me_id="claude", participants=PARTICIPANTS)
    # The argument itself is NEVER rewritten to hide the leak — the whole
    # point is honesty about the round being compromised, not disguising it.
    assert r["text"] == original
    assert r["compromised"] is True
    assert any(l["term"] == "Gemini" for l in r["leaks"])


def test_guard_response_self_descriptive_leak_without_any_name():
    r = IG.guard_response("Well, as the 7B seat here, my weights are small.",
                          me_id="flint", participants=PARTICIPANTS)
    assert r["compromised"] is True
    assert any(l.get("self_descriptive") for l in r["leaks"])


ALL_TESTS = [
    test_mapping_is_not_derivable_from_session_id,
    test_reveal_is_manual_and_nothing_else_flips_it,
    test_compromise_is_recorded_never_silent,
    test_fresh_experiment_scope_is_empty_pre_existing_history_never_shown,
    test_in_scope_round_relabelled_no_real_name_in_rendered_history,
    test_residual_identity_in_scoped_round_text_fails_closed,
    test_original_session_record_is_never_rewritten,
    test_audit_prompt_flags_another_participants_name,
    test_audit_prompt_allows_recipients_own_identity_terms_elsewhere,
    test_audit_prompt_catches_provider_and_model_leak_not_just_name,
    test_audit_all_prompts_gate_is_all_or_nothing,
    test_strip_identity_header_removes_only_the_leading_announcement,
    test_strip_identity_header_never_touches_mid_message_text,
    test_guard_response_header_removed_and_clean,
    test_guard_response_prose_leak_is_reported_not_edited,
    test_guard_response_self_descriptive_leak_without_any_name,
]

if __name__ == "__main__":
    for t in ALL_TESTS:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\nALL PASS ({len(ALL_TESTS)} tests)")
