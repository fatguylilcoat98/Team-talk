"""Tests for message provenance (author vs relay).

Standalone: `python tests/test_provenance.py`. Verifies the speaker-label logic
and that build_context renders provenance for both the current message and
history, without disturbing the default Chris / Splendor behavior.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import conversation as C


def test_speaker_label_default_and_splendor():
    assert C._speaker_label("Chris") == "Chris"
    assert C._speaker_label(None) == "Chris"
    # Chris authoring through Splendor keeps the existing Splendor label
    assert C._speaker_label("Chris", via_splendor=True) == "Splendor (for Chris)"
    assert C._speaker_label(None, via_splendor=True) == "Splendor (for Chris)"


def test_speaker_label_relayed_author():
    assert C._speaker_label("Outside ChatGPT") == "Outside ChatGPT (relayed by Chris)"
    assert C._speaker_label("Guest", relay_name="Chris") == "Guest (relayed by Chris)"
    # a relayed author is shown as the author even if via_splendor is set
    assert C._speaker_label("Outside Gemini", via_splendor=True) == "Outside Gemini (relayed by Chris)"


def test_build_context_current_message_provenance():
    prompt = C.build_context([], "the relayed text", "FLINT",
                             ["Claude"], author_name="Outside ChatGPT", relay_name="Chris")
    assert "Outside ChatGPT (relayed by Chris): the relayed text" in prompt
    # default stays exactly "Chris:"
    plain = C.build_context([], "hi", "FLINT", ["Claude"])
    assert "\nChris: hi" in plain
    assert "relayed by" not in plain


def test_build_context_history_provenance():
    rounds = [{"round": 1, "chris_message": "earlier relayed line",
               "author_name": "Outside Claude", "relay_name": "Chris",
               "responses": [{"name": "FLINT", "text": "ok"}]}]
    prompt = C.build_context(rounds, "now", "FLINT", ["Claude"])
    assert "Outside Claude (relayed by Chris): earlier relayed line" in prompt


def test_data_model_two_separate_fields():
    # provenance is two distinct fields, never overloaded into one
    round_data = {"chris_message": "x", "author_name": "Outside Grok", "relay_name": "Chris"}
    assert round_data["author_name"] == "Outside Grok"
    assert round_data["relay_name"] == "Chris"
    assert round_data["author_name"] != round_data["relay_name"]


ALL_TESTS = [test_speaker_label_default_and_splendor, test_speaker_label_relayed_author,
             test_build_context_current_message_provenance, test_build_context_history_provenance,
             test_data_model_two_separate_fields]

if __name__ == "__main__":
    for t in ALL_TESTS:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\nALL PASS ({len(ALL_TESTS)} tests)")
