"""Reveal-aware export gating: html/markdown/pdf must stay anonymous before
a blind experiment's manual reveal, and resolve to "Voice N — Name" after —
across every export path, not only PDF (pdf_export.py already preferred
`label` over `name`; the gap this closes is that none of the three formats
knew how to resolve a label back to a name once the room chose to reveal).

Standalone (no pytest): `python tests/test_export_reveal.py`.
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import blind_experiment as BE
import ledger
import session_manager as SM

_TMP = None

PARTICIPANTS = [
    {"id": "claude", "name": "Claude"},
    {"id": "gemini", "name": "Gemini"},
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


def _make_session(exp_id, label_map):
    return {
        "id": "sess-export-1",
        "created_at": "2026-07-19T00:00:00Z",
        "rounds": [
            {"round": 1, "timestamp": "2026-07-19T00:00:00Z",
             "chris_message": "thoughts?", "modes": ["blind"],
             "blind_experiment_id": exp_id,
             "responses": [
                 {"id": "claude", "name": "Claude", "label": label_map["claude"],
                  "text": "Short TTLs are safer.", "color": "#8a93a5"},
                 {"id": "gemini", "name": "Gemini", "label": label_map["gemini"],
                  "text": "Aggressive caching wins on speed.", "color": "#8a93a5"},
             ]},
            {"round": 2, "timestamp": "2026-07-19T00:05:00Z",
             "chris_message": "and normally?", "modes": ["collab"],
             "responses": [
                 {"id": "claude", "name": "Claude", "text": "Same take, out loud.",
                  "color": "#d97757"},
             ]},
        ],
    }


def test_all_three_export_formats_stay_anonymous_before_reveal():
    _fresh()
    exp = BE.open_experiment("sess-export-1", PARTICIPANTS)
    BE.record_round(exp["experiment_id"], "sess-export-1", 1)
    full = BE.get(exp["experiment_id"])
    session = _make_session(exp["experiment_id"], full["mapping"])

    html = SM.export_html(session)
    md = SM.export_markdown(session)
    pdf = SM.export_pdf(session)

    for label in full["mapping"].values():
        assert label in html and label in md
    for name in ("Claude", "Gemini"):
        assert name not in html.split("Round 2")[0], f"{name} leaked into export before reveal"
        assert name not in md.split("Round 2")[0]
    # Round 2 (normal, non-blind) is unaffected — real name still shown.
    assert "Claude" in html.split("Round 2", 1)[1]
    assert isinstance(pdf, (bytes, bytearray)) and len(pdf) > 100
    _cleanup()


def test_exports_resolve_to_name_after_manual_reveal():
    _fresh()
    exp = BE.open_experiment("sess-export-1", PARTICIPANTS)
    BE.record_round(exp["experiment_id"], "sess-export-1", 1)
    full = BE.get(exp["experiment_id"])
    session = _make_session(exp["experiment_id"], full["mapping"])

    BE.reveal(exp["experiment_id"], by="chris")

    html = SM.export_html(session)
    md = SM.export_markdown(session)
    for pid, name in (("claude", "Claude"), ("gemini", "Gemini")):
        label = full["mapping"][pid]
        assert f"{label} — {name}" in html
        assert f"{label} — {name}" in md
    _cleanup()


def test_reveal_never_mutates_the_stored_session_object():
    _fresh()
    exp = BE.open_experiment("sess-export-1", PARTICIPANTS)
    BE.record_round(exp["experiment_id"], "sess-export-1", 1)
    full = BE.get(exp["experiment_id"])
    session = _make_session(exp["experiment_id"], full["mapping"])
    before = session["rounds"][0]["responses"][0]["label"]

    BE.reveal(exp["experiment_id"], by="chris")
    SM.export_html(session)   # exporting must not mutate the caller's dict

    assert session["rounds"][0]["responses"][0]["label"] == before
    _cleanup()


def test_unknown_or_unreadable_experiment_fails_closed_stays_anonymous():
    _fresh()
    # blind_experiment_id points at nothing real (corrupt/missing record) —
    # export must never GUESS revealed; it must default to sealed.
    session = _make_session("blind_doesnotexist", {"claude": "Voice 1", "gemini": "Voice 2"})
    html = SM.export_html(session)
    assert "Voice 1" in html and "Voice 2" in html
    assert "Claude" not in html.split("Round 2")[0]
    assert "Gemini" not in html.split("Round 2")[0]
    _cleanup()


def test_normal_session_with_no_blind_rounds_completely_unaffected():
    _fresh()
    session = {
        "id": "sess-normal", "created_at": "2026-07-19T00:00:00Z",
        "rounds": [{"round": 1, "timestamp": "2026-07-19T00:00:00Z",
                   "chris_message": "hi", "modes": ["collab"],
                   "responses": [{"id": "claude", "name": "Claude", "text": "hello",
                                 "color": "#d97757"}]}],
    }
    html = SM.export_html(session)
    md = SM.export_markdown(session)
    assert "Claude" in html and "Claude" in md
    _cleanup()


ALL_TESTS = [
    test_all_three_export_formats_stay_anonymous_before_reveal,
    test_exports_resolve_to_name_after_manual_reveal,
    test_reveal_never_mutates_the_stored_session_object,
    test_unknown_or_unreadable_experiment_fails_closed_stays_anonymous,
    test_normal_session_with_no_blind_rounds_completely_unaffected,
]

if __name__ == "__main__":
    for t in ALL_TESTS:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\nALL PASS ({len(ALL_TESTS)} tests)")
