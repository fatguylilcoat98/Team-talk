"""Tests for the Pattern Catcher's read-only ledger query and the office that owns it.

Standalone (no pytest): `python tests/test_ledger_query.py`. Session/store paths are
re-pointed at a throwaway temp dir so nothing touches real room data — the same way
test_reasoning_ledger.py does it.

The essential receipts: the query never writes, and it refuses to call several
similar-sounding messages a consensus.
"""

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ledger
import ledger_query as LQ
import office_store as OS

_TMP = None


def _fresh():
    """A tiny archive with a known shape: two sessions, several seats, one
    correction, one dissent, one explicit decision by Chris."""
    global _TMP
    _TMP = tempfile.mkdtemp()
    LQ.SESSIONS_DIR = os.path.join(_TMP, "sessions")
    os.makedirs(LQ.SESSIONS_DIR, exist_ok=True)
    ledger.LEDGER_DIR = _TMP
    ledger.LEDGER_PATH = os.path.join(_TMP, "ledger.jsonl")
    OS.STORE_DIR = _TMP
    OS.OFFICES_PATH = os.path.join(_TMP, "offices.jsonl")

    s1 = {"id": "team-talk-aaa", "created_at": "2026-07-01T00:00:00Z", "rounds": [
        {"round": 1, "timestamp": "2026-07-01T10:00:00Z", "chris_message": "thoughts on caching?",
         "modes": ["collab"], "responses": [
             {"id": "claude", "name": "Claude", "text": "We should cache aggressively for speed."},
             {"id": "gemini", "name": "Gemini", "text": "I agree, caching is the right call here."},
             {"id": "grok", "name": "Grok", "text": "I disagree, caching will cause stale reads."},
         ]},
        {"round": 2, "timestamp": "2026-07-01T11:00:00Z", "chris_message": "still caching",
         "modes": ["collab"], "responses": [
             {"id": "claude", "name": "Claude",
              "text": "Correction: I was wrong about caching aggressively — stale reads matter."},
         ]},
    ]}
    s2 = {"id": "team-talk-bbb", "created_at": "2026-07-02T00:00:00Z", "rounds": [
        {"round": 1, "timestamp": "2026-07-02T10:00:00Z", "chris_message": "decide it",
         "modes": ["collab"], "responses": [
             {"id": "chris", "name": "Chris", "text": "Decision: we cache with a 60s TTL."},
             {"id": "flint", "name": "FLINT", "text": "Understood, a bounded TTL is defensible."},
         ]},
    ]}
    for s in (s1, s2):
        with open(os.path.join(LQ.SESSIONS_DIR, f"{s['id']}.json"), "w", encoding="utf-8") as f:
            json.dump(s, f)


def _cleanup():
    if _TMP and os.path.isdir(_TMP):
        shutil.rmtree(_TMP, ignore_errors=True)


# ---- search ------------------------------------------------------------------

def test_exact_phrase_search():
    r = LQ.search("cache aggressively")
    assert r["ok"] and r["total"] >= 1, r
    assert any("aggressively" in x["excerpt"] for x in r["results"])


def test_keyword_search_matches_all_words_in_any_order():
    r = LQ.search("stale caching")
    assert r["total"] >= 1, r


def test_participant_filter():
    r = LQ.search("caching", participant="grok")
    assert r["total"] == 1, r
    assert r["results"][0]["participant"] == "Grok"


def test_participant_filter_accepts_display_name():
    assert LQ.search("caching", participant="Grok")["total"] == 1


def test_round_filter():
    assert LQ.search("caching", round_min=2)["total"] >= 1
    assert LQ.search("caching", round_min=99)["total"] == 0


def test_session_filter():
    r = LQ.search("cache", session="team-talk-bbb")
    assert r["total"] >= 1
    assert all(x["session"] == "team-talk-bbb" for x in r["results"])


def test_status_filter_finds_the_correction():
    r = LQ.search("caching", status=LQ.CORRECTED)
    assert r["total"] >= 1, r
    assert any(LQ.CORRECTED in x["status_markers"] for x in r["results"])


def test_status_filter_finds_dissent():
    r = LQ.search("caching", status=LQ.DISPUTED)
    assert any(x["participant"] == "Grok" for x in r["results"]), r


def test_no_result_is_a_clean_empty_not_an_error():
    r = LQ.search("zzz-nothing-matches-zzz")
    assert r["ok"] is True and r["total"] == 0 and r["results"] == []


def test_malformed_query_is_rejected_with_a_reason():
    bad = LQ.search("x", status="not-a-status")
    assert bad["ok"] is False and bad["errors"]
    assert LQ.search("x", round_min=5, round_max=2)["ok"] is False
    assert LQ.search("x", since="yesterday")["ok"] is False


def test_result_limit_and_pagination():
    r = LQ.search("caching", limit=1)
    assert r["returned"] == 1 and r["truncated"] is True
    p2 = LQ.search("caching", limit=1, offset=1)
    assert p2["results"][0]["ref"] != r["results"][0]["ref"]


def test_limit_is_hard_capped():
    r = LQ.search("caching", limit=10_000)
    assert r["query"]["limit"] <= LQ.MAX_LIMIT


def test_every_result_carries_provenance():
    for x in LQ.search("caching")["results"]:
        for key in ("session", "round", "participant", "timestamp", "excerpt", "ref", "kind"):
            assert key in x, key
        assert x["ref"].startswith(("session:", "ledger:", "claim:", "participation:"))


def test_open_original_resolves_a_ref_to_the_full_record():
    hit = LQ.search("stale reads", participant="grok")["results"][0]
    orig = LQ.open_original(hit["ref"])
    assert orig["ok"] and orig["record"]["id"] == "grok"
    assert "stale reads" in orig["record"]["text"]


def test_open_original_rejects_a_bad_ref():
    assert LQ.open_original("garbage")["ok"] is False
    assert LQ.open_original("session:nope#round=1&seat=x")["ok"] is False


def test_excerpt_is_verbatim_never_a_summary():
    hit = LQ.search("stale reads", participant="grok")["results"][0]
    full = LQ.open_original(hit["ref"])["record"]["text"]
    assert hit["excerpt"].strip("…").strip() in full


# ---- read-only guarantee ------------------------------------------------------

def test_module_exposes_no_write_surface():
    """The read-only promise, enforced structurally rather than by good intentions."""
    banned = ("append", "write", "save", "update", "delete", "record_", "set_", "mutate")
    public = [n for n in dir(LQ) if not n.startswith("_")]
    offenders = [n for n in public if callable(getattr(LQ, n))
                 and any(n.lower().startswith(b) for b in banned)]
    assert not offenders, f"write-shaped functions exported: {offenders}"


def test_searching_does_not_alter_the_archive():
    before = {}
    for fn in os.listdir(LQ.SESSIONS_DIR):
        p = os.path.join(LQ.SESSIONS_DIR, fn)
        before[fn] = (os.path.getsize(p), open(p, encoding="utf-8").read())
    LQ.search("caching")
    LQ.consensus_drift("caching")
    for fn, (size, body) in before.items():
        p = os.path.join(LQ.SESSIONS_DIR, fn)
        assert os.path.getsize(p) == size and open(p, encoding="utf-8").read() == body, fn


# ---- consensus vs repeated language -------------------------------------------

def test_dissent_prevents_a_consensus_finding():
    d = LQ.consensus_drift("caching")
    assert d["interpretation"]["agreement_kind"] != LQ.RECORDED_CONSENSUS
    assert d["interpretation"]["distinct_dissenters"] >= 1


def test_repeated_language_alone_is_not_consensus():
    """One seat repeating itself must never read as the room agreeing."""
    s = {"id": "team-talk-ccc", "rounds": [
        {"round": i, "timestamp": f"2026-07-05T0{i}:00:00Z", "chris_message": "?",
         "responses": [{"id": "claude", "name": "Claude",
                        "text": "Widgets are clearly the best approach."}]}
        for i in range(1, 5)]}
    with open(os.path.join(LQ.SESSIONS_DIR, "team-talk-ccc.json"), "w", encoding="utf-8") as f:
        json.dump(s, f)
    d = LQ.consensus_drift("widgets")
    assert d["interpretation"]["agreement_kind"] == LQ.REPEATED_LANGUAGE
    assert d["interpretation"]["distinct_supporters"] < LQ.MIN_DISTINCT_SUPPORTERS
    assert any("NOT consensus" in n for n in d["interpretation"]["notes"])


def test_founder_decision_outranks_prose_agreement():
    d = LQ.consensus_drift("TTL")
    assert d["interpretation"]["agreement_kind"] == LQ.FOUNDER_DECISION
    assert d["interpretation"]["confidence_that_consensus_existed"] == "high"


def test_corrections_and_retractions_are_retrievable():
    d = LQ.consensus_drift("caching")
    assert d["evidence"]["corrections_or_retractions"], d["evidence"]
    assert any("I was wrong" in c["excerpt"]
               for c in d["evidence"]["corrections_or_retractions"])


def test_evidence_and_interpretation_are_separated():
    d = LQ.consensus_drift("caching")
    assert set(d) >= {"evidence", "interpretation"}
    assert "disclaimer" in d["interpretation"]
    # nothing generated may sit inside the evidence block
    assert "agreement_kind" not in d["evidence"]


def test_no_records_yields_uncertainty_not_a_conclusion():
    d = LQ.consensus_drift("a topic nobody ever discussed")
    assert d["interpretation"]["agreement_kind"] is None
    assert d["interpretation"]["confidence_that_consensus_existed"] == "none"
    assert any("Absence of records" in n for n in d["interpretation"]["notes"])


def test_unreadable_session_is_reported_not_hidden():
    """Incomplete records must produce stated uncertainty, never a quiet partial answer."""
    with open(os.path.join(LQ.SESSIONS_DIR, "team-talk-broken.json"), "w",
              encoding="utf-8") as f:
        f.write("{ this is not valid json")
    r = LQ.search("caching")
    assert r["incomplete"], "an unreadable session must be surfaced"
    d = LQ.consensus_drift("caching")
    assert any("PARTIAL" in n for n in d["interpretation"]["notes"])


def test_result_count_is_not_evidence_of_truth():
    """A count is a count. The report must never upgrade confidence on volume alone."""
    d = LQ.consensus_drift("widgets")   # many matches, one speaker
    assert d["evidence"]["matched_records"] >= 3
    assert d["interpretation"]["confidence_that_consensus_existed"] == "low"


# ---- the office ---------------------------------------------------------------

def test_office_capability_belongs_to_the_seat_not_the_model():
    OS.assign(OS.PATTERN_CATCHER, "deepseek")
    assert OS.holds(OS.PATTERN_CATCHER, "deepseek")
    assert "ledger_query" in OS.capabilities_for("deepseek")
    assert OS.capabilities_for("claude") == []
    # hand the office to another model — capability moves, office survives
    OS.assign(OS.PATTERN_CATCHER, "gemini")
    assert OS.capabilities_for("deepseek") == []
    assert "ledger_query" in OS.capabilities_for("gemini")


def test_office_history_survives_reassignment():
    hist = OS.history(OS.PATTERN_CATCHER)
    assert [h["participant_id"] for h in hist] == ["deepseek", "gemini"]
    assert OS.occupant(OS.PATTERN_CATCHER) == "gemini"


def test_office_can_be_vacated_without_losing_history():
    OS.assign(OS.PATTERN_CATCHER, None)
    assert OS.occupant(OS.PATTERN_CATCHER) is None
    assert OS.capabilities_for("gemini") == []
    assert len(OS.history(OS.PATTERN_CATCHER)) == 3


def test_unknown_office_is_rejected():
    try:
        OS.assign("ministry_of_silly_walks", "claude")
    except ValueError:
        return
    raise AssertionError("an unknown office must be rejected")


ALL_TESTS = [
    test_exact_phrase_search,
    test_keyword_search_matches_all_words_in_any_order,
    test_participant_filter,
    test_participant_filter_accepts_display_name,
    test_round_filter,
    test_session_filter,
    test_status_filter_finds_the_correction,
    test_status_filter_finds_dissent,
    test_no_result_is_a_clean_empty_not_an_error,
    test_malformed_query_is_rejected_with_a_reason,
    test_result_limit_and_pagination,
    test_limit_is_hard_capped,
    test_every_result_carries_provenance,
    test_open_original_resolves_a_ref_to_the_full_record,
    test_open_original_rejects_a_bad_ref,
    test_excerpt_is_verbatim_never_a_summary,
    test_module_exposes_no_write_surface,
    test_searching_does_not_alter_the_archive,
    test_dissent_prevents_a_consensus_finding,
    test_repeated_language_alone_is_not_consensus,
    test_founder_decision_outranks_prose_agreement,
    test_corrections_and_retractions_are_retrievable,
    test_evidence_and_interpretation_are_separated,
    test_no_records_yields_uncertainty_not_a_conclusion,
    test_unreadable_session_is_reported_not_hidden,
    test_result_count_is_not_evidence_of_truth,
    test_office_capability_belongs_to_the_seat_not_the_model,
    test_office_history_survives_reassignment,
    test_office_can_be_vacated_without_losing_history,
    test_unknown_office_is_rejected,
]

if __name__ == "__main__":
    _fresh()
    failures = 0
    try:
        for t in ALL_TESTS:
            try:
                t()
                print(f"  ok  {t.__name__}")
            except Exception as e:  # noqa: BLE001
                failures += 1
                print(f"FAIL  {t.__name__}: {type(e).__name__}: {e}")
    finally:
        _cleanup()
    print(f"\n{'ALL PASS' if not failures else 'FAILURES'} "
          f"({len(ALL_TESTS) - failures}/{len(ALL_TESTS)})")
    sys.exit(1 if failures else 0)
