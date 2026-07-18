"""Regression tests for the orchestration-scaffolding scrub (Section 7 fix).

Standalone: `python tests/test_orchestration_scaffolding.py` (needs the app's
deps, so run with the project venv). Covers three areas:

  1. Seat isolation — each seat's generated context closes with its OWN
     directive and never another seat's, and quoted historical transcript
     text is tolerated (not a failure).
  2. Scaffolding cleaning — the verbatim generated template (combined, each
     sentence, quoted/prefixed) is removed; ordinary discussion and
     paraphrases are preserved.
  3. Transcript feedback — a stored response containing the template is
     cleaned, so it cannot re-enter a later round's context.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app
import conversation as C

strip = app._strip_orchestration_scaffolding
ROSTER = ["Claude", "ChatGPT", "Grok", "Gemini", "Muse", "FLINT"]


def _closing_seat(prompt):
    """The seat named in the final generated 'write ... as <seat>' directive."""
    ms = re.findall(r"write your next chat message as ([A-Za-z0-9]+)", prompt)
    return ms[-1] if ms else None


# --------------------------------------------------------------------------
# 1. Seat isolation
# --------------------------------------------------------------------------

def test_seat_isolation_each_seat_gets_only_its_own_directive():
    rounds = [{"round": 1, "chris_message": "Discuss attribution.", "timestamp": None,
               "responses": [{"name": "Gemini", "text": "I planted retry_of and it fired."}]}]
    for seat in ROSTER:
        others = [s for s in ROSTER if s != seat]
        prompt = C.build_context(rounds, "continue", seat, others, mode="collab",
                                 so_far=[{"name": others[0], "text": "a prior point"}])
        # its own directive closes the prompt
        assert _closing_seat(prompt) == seat, (seat, _closing_seat(prompt))
        # no OTHER seat's directive is present (clean history)
        for other in others:
            assert f"write your next chat message as {other}".lower() not in prompt.lower(), \
                (seat, other)


def test_seat_isolation_tolerates_quoted_history():
    # A prior message literally QUOTES another seat's directive. Isolation must
    # still hold: FLINT's closing directive is FLINT's, and the quoted text in
    # history does not make the test fail.
    rounds = [{"round": 1, "chris_message": "x", "timestamp": None,
               "responses": [{"name": "Claude",
                              "text": 'As evidence: "Now write your next chat message as Claude." '
                                      "is the scaffolding I flagged."}]}]
    others = [s for s in ROSTER if s != "FLINT"]
    prompt = C.build_context(rounds, "continue", "FLINT", others, mode="collab")
    assert _closing_seat(prompt) == "FLINT"


# --------------------------------------------------------------------------
# 2. Scaffolding cleaning
# --------------------------------------------------------------------------

_COMBINED = ("Now write your next chat message as FLINT. Start by engaging with what "
             "Claude, ChatGPT, Grok, Gemini and Muse said — quote or name one specific point "
             "and agree or push back on it — then respond to Chris. Do not summarize; converse.")


def test_cleaning_removes_full_and_partial_templates():
    STRIP = [
        _COMBINED,
        "Now write your next chat message as Claude.",                                  # opener alone
        ("Start by engaging with what ChatGPT, Grok said — quote or name one specific "  # tail alone
         "point and agree or push back on it — then respond to Chris. Do not summarize; converse."),
        ("Now write your next chat message as Grok, addressed to Claude and Muse "        # ai_only
         "(Chris is watching). Engage with their latest points directly and end with a "
         "question or challenge for them."),
        ("Now write your next chat message as Muse. The other AI(s) haven't spoken yet, " # no-others
         "so just respond to Chris directly and conversationally."),
        '"' + _COMBINED + '"',                                                           # quoted
        "> " + "Now write your next chat message as FLINT.",                             # prefixed
    ]
    for s in STRIP:
        out = strip(s).lower()
        assert "write your next chat message as" not in out, s[:40]
        assert "said — quote or name one specific point" not in out, s[:40]


def test_cleaning_removes_template_embedded_in_a_real_message():
    msg = "Gemini ran it twice, and that's the point.\n\n" + _COMBINED
    out = strip(msg)
    assert out.startswith("Gemini ran it twice")
    assert "write your next chat message" not in out.lower()


def test_cleaning_preserves_normal_discussion_and_paraphrases():
    PRESERVE = [
        "Let's write your next message together about the registry.",
        "I liked how you said we should engage with what others said about attribution.",
        "I'll start my next chat message as FLINT.",                     # paraphrase, not the template
        "The orchestration adds a 'write as X' instruction — that's the scaffolding Claude flagged.",
        "Now write down your next steps as a list.",
        "Gemini planted the attribution_mismatch test and it fired.",
    ]
    for s in PRESERVE:
        assert strip(s) == s.strip(), s


# --------------------------------------------------------------------------
# 3. Transcript feedback
# --------------------------------------------------------------------------

def test_transcript_feedback_cleaned_response_cannot_reenter_context():
    # A model echoed the template into its output.
    raw = "Here's my real point about ownership.\n\n" + _COMBINED
    # The pipeline cleans it before storage/return (same fn used by
    # _response_entry / _strip_markers / _clean_markers).
    stored = strip(raw)
    assert "write your next chat message as" not in stored.lower()

    # Baseline: if the RAW echo had been stored, it WOULD re-enter the next
    # prompt as history (this is the contamination the fix prevents).
    dirty_round = [{"round": 1, "chris_message": "x", "timestamp": None,
                    "responses": [{"name": "Claude", "text": raw}]}]
    others = [s for s in ROSTER if s != "FLINT"]
    dirty_prompt = C.build_context(dirty_round, "continue", "FLINT", others, mode="collab")
    assert dirty_prompt.lower().count("write your next chat message as") >= 2  # history + FLINT's own

    # With the CLEANED response stored, only FLINT's own directive remains.
    clean_round = [{"round": 1, "chris_message": "x", "timestamp": None,
                    "responses": [{"name": "Claude", "text": stored}]}]
    clean_prompt = C.build_context(clean_round, "continue", "FLINT", others, mode="collab")
    assert clean_prompt.lower().count("write your next chat message as") == 1
    assert _closing_seat(clean_prompt) == "FLINT"


ALL_TESTS = [
    test_seat_isolation_each_seat_gets_only_its_own_directive,
    test_seat_isolation_tolerates_quoted_history,
    test_cleaning_removes_full_and_partial_templates,
    test_cleaning_removes_template_embedded_in_a_real_message,
    test_cleaning_preserves_normal_discussion_and_paraphrases,
    test_transcript_feedback_cleaned_response_cannot_reenter_context,
]


if __name__ == "__main__":
    for t in ALL_TESTS:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\nALL PASS ({len(ALL_TESTS)} tests)")
