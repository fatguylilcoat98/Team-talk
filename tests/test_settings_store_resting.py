"""Regression test for the resting/max_tokens read-path bug found during
Phase 1 production verification: sanitize_participants() (write path)
always saved `resting` and `max_tokens` correctly, but get_participants()
(read path — everything else in the app, including _chat_impl's "who's
active" filter, calls this) silently stripped both fields back out because
PARTICIPANT_FIELDS never listed them. A rested seat never actually rested.

Standalone (no pytest): `python tests/test_settings_store_resting.py`.
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import settings_store as SS

_TMP = None


def _fresh():
    global _TMP
    _TMP = tempfile.mkdtemp()
    SS.CONFIG_DIR = _TMP
    SS.SETTINGS_PATH = os.path.join(_TMP, "settings.json")


def _cleanup():
    if _TMP and os.path.isdir(_TMP):
        shutil.rmtree(_TMP, ignore_errors=True)


def test_resting_survives_the_round_trip():
    _fresh()
    incoming = [
        {"name": "Claude", "model": "claude-opus", "provider": "anthropic", "resting": True},
        {"name": "Grok", "model": "grok-4", "provider": "openai", "resting": False},
    ]
    roster = SS.sanitize_participants(incoming)
    SS.save({"participants": roster})

    read_back = {p["name"]: p for p in SS.get_participants()}
    assert read_back["Claude"]["resting"] is True, "resting=True must survive get_participants()"
    assert not read_back["Grok"].get("resting"), "an unrested seat must not read back resting"
    _cleanup()


def test_max_tokens_survives_the_round_trip():
    _fresh()
    incoming = [{"name": "Claude", "model": "claude-opus", "provider": "anthropic", "max_tokens": 4000}]
    roster = SS.sanitize_participants(incoming)
    SS.save({"participants": roster})
    read_back = SS.get_participants()[0]
    assert read_back.get("max_tokens") == 4000
    _cleanup()


def test_a_rested_seat_is_actually_excluded_from_the_active_roster():
    _fresh()
    incoming = [
        {"name": "Claude", "model": "claude-opus", "provider": "anthropic", "resting": True},
        {"name": "Grok", "model": "grok-4", "provider": "openai"},
    ]
    roster = SS.sanitize_participants(incoming)
    SS.save({"participants": roster})
    active = [p for p in SS.get_participants() if not p.get("resting")]
    assert [p["name"] for p in active] == ["Grok"]
    _cleanup()


ALL_TESTS = [
    test_resting_survives_the_round_trip,
    test_max_tokens_survives_the_round_trip,
    test_a_rested_seat_is_actually_excluded_from_the_active_roster,
]

if __name__ == "__main__":
    for t in ALL_TESTS:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\nALL PASS ({len(ALL_TESTS)} tests)")
