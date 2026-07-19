"""Integration tests for blind mode over the REAL /api/chat turn path —
not blind_context/identity_guard called directly (see test_blind_mechanism.py
for that), but the actual FastAPI route app.chat() -> app._chat_impl(),
exactly as a browser would call it. api_client.call_participant is stubbed
(no network); every other store is redirected to a temp dir
(tests/_test_isolation.py) so nothing touches this laptop's real data.

Standalone (no pytest): `python tests/test_blind_live_turn.py`, from the
project venv (needs fastapi/httpx, already a project dependency).
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

import _test_isolation as ISO

_TMP = None
_CLIENT = None


def _fresh(roster=None):
    global _TMP, _CLIENT
    _TMP = tempfile.mkdtemp()
    ISO.redirect_all_stores(_TMP)
    import settings_store
    settings_store.get_participants = lambda: list(roster or ISO.ROSTER)
    import app
    _CLIENT = TestClient(app.app)
    return app


def _cleanup():
    if _TMP and os.path.isdir(_TMP):
        shutil.rmtree(_TMP, ignore_errors=True)


def _names_and_ids(roster):
    terms = []
    for p in roster:
        terms += [p["name"], p["id"], p["model"], p["provider"]]
    return terms


def _assert_no_leak(term, haystack, who):
    assert term.lower() not in haystack.lower(), f"leaked {term!r} into {who}'s prompt"


# --------------------------------------------------------------------------
# Normal mode: byte-for-byte unaffected by the blind wiring
# --------------------------------------------------------------------------

def test_normal_mode_response_shape_unchanged():
    app = _fresh()
    stub = ISO.make_stub_call_participant("a normal reply")
    app.api_client.call_participant = stub
    r = _CLIENT.post("/api/chat", json={"message": "hello room", "modes": ["collab"]})
    assert r.status_code == 200, r.text
    body = r.json()
    for resp in body["responses"]:
        assert "label" not in resp
        assert resp["name"] in ("Claude", "Gemini", "FLINT")
        assert resp["color"] != "#8a93a5"   # the blind neutral color never applied
    assert "blind_experiment_id" not in body
    _cleanup()


# --------------------------------------------------------------------------
# Blind mode: the live six-prompt, zero-leak check
# --------------------------------------------------------------------------

def test_blind_two_rounds_six_prompts_zero_identity_leaks():
    app = _fresh()
    roster = ISO.ROSTER
    # Realistic blind-compliant replies: an opinion, no self-naming, no
    # naming of anyone else — exactly what the "blind" mode system prompt
    # instructs every seat to do.
    takes = ["Short TTLs are safer than aggressive caching.",
             "I'd rather cache aggressively and accept some staleness.",
             "Both approaches work; it depends on read/write ratio."]
    stub = ISO.make_stub_call_participant(
        per_participant={p["id"]: takes[i] for i, p in enumerate(roster)})
    app.api_client.call_participant = stub

    r1 = _CLIENT.post("/api/chat", json={"message": "round one", "modes": ["blind"]})
    assert r1.status_code == 200, r1.text
    sid = r1.json()["session_id"]
    for resp in r1.json()["responses"]:
        assert resp["label"].startswith("Voice ")
        assert resp["color"] == "#8a93a5"
    assert r1.json()["blind_experiment_id"]

    r2 = _CLIENT.post("/api/chat", json={"message": "round two", "session_id": sid,
                                         "modes": ["blind"]})
    assert r2.status_code == 200, r2.text

    assert len(stub.calls) == 2 * len(roster), "expected six outbound prompts (3 seats x 2 rounds)"
    for pid, system, prompt in stub.calls:
        full = f"{system}\n\n{prompt}"
        for other in roster:
            if other["id"] == pid:
                continue
            for term in (other["name"], other["model"]):
                _assert_no_leak(term, full, pid)

    status = _CLIENT.get(f"/api/blind/{sid}").json()
    assert status["active"] is True
    assert status["rounds"] == [1, 2]
    assert status["compromised"] is False
    assert status["sealed"] is True
    _cleanup()


def test_blind_scoped_history_hides_round_one_identity_from_round_two_prompt():
    # The regression test for the specific 25-leak defect: round 2's
    # CONVERSATION HISTORY must show Voice labels for round 1, never names.
    app = _fresh()
    roster = ISO.ROSTER
    takes = ["Short TTLs are safer.", "Aggressive caching wins on speed.",
             "It depends on read/write ratio."]
    stub = ISO.make_stub_call_participant(
        per_participant={p["id"]: takes[i] for i, p in enumerate(roster)})
    app.api_client.call_participant = stub
    r1 = _CLIENT.post("/api/chat", json={"message": "round one", "modes": ["blind"]})
    sid = r1.json()["session_id"]
    stub.calls.clear()
    r2 = _CLIENT.post("/api/chat", json={"message": "round two", "session_id": sid,
                                         "modes": ["blind"]})
    assert r2.status_code == 200, r2.text
    for pid, system, prompt in stub.calls:
        assert "CONVERSATION HISTORY" in prompt
        # The recipient's OWN name may legitimately appear (e.g. inside their
        # own private journal boot block, never shown to anyone else) — only
        # OTHER participants' names must never appear.
        for other in roster:
            if other["id"] == pid:
                continue
            assert other["name"] not in prompt, (pid, other["name"])
    _cleanup()


# --------------------------------------------------------------------------
# Fail-closed paths
# --------------------------------------------------------------------------

def test_dirty_history_from_a_leaked_reply_fails_closed_next_round():
    # Round 1: FLINT's reply mentions Gemini by name in PROSE. guard_response
    # cannot un-send it (already generated) — it marks the experiment
    # compromised but the round still saves (honest record, not edited).
    # Round 2 must then refuse to run at all: the leaked mention is now
    # in-scope history, and prepare_blind_turn's residual scan must catch it.
    app = _fresh()
    roster = ISO.ROSTER
    stub = ISO.make_stub_call_participant(per_participant={
        "claude": "Nothing unusual here.",
        "gemini": "Agreed, nothing unusual.",
        "flint": "I think Gemini's take is actually the strongest one.",
    })
    app.api_client.call_participant = stub
    r1 = _CLIENT.post("/api/chat", json={"message": "round one", "modes": ["blind"]})
    assert r1.status_code == 200, r1.text
    sid = r1.json()["session_id"]

    status = _CLIENT.get(f"/api/blind/{sid}").json()
    assert status["compromised"] is True, "prose leak must mark the experiment compromised"

    r2 = _CLIENT.post("/api/chat", json={"message": "round two", "session_id": sid,
                                         "modes": ["blind"]})
    assert r2.status_code == 409, r2.text
    assert "safe_to_run" in r2.text or "identity" in r2.text.lower()
    _cleanup()


def test_roster_mismatch_fails_closed():
    app = _fresh(roster=ISO.ROSTER[:2])   # open with only claude + gemini
    stub = ISO.make_stub_call_participant("fine")
    app.api_client.call_participant = stub
    r1 = _CLIENT.post("/api/chat", json={"message": "round one", "modes": ["blind"]})
    assert r1.status_code == 200, r1.text
    sid = r1.json()["session_id"]

    # FLINT joins the roster mid-experiment — not covered by the sealed mapping.
    import settings_store
    settings_store.get_participants = lambda: list(ISO.ROSTER)
    r2 = _CLIENT.post("/api/chat", json={"message": "round two", "session_id": sid,
                                         "modes": ["blind"]})
    assert r2.status_code == 409, r2.text
    assert len(stub.calls) == 2, "no participant should have been called on the mismatched round"
    _cleanup()


def test_failed_closed_round_is_never_persisted():
    app = _fresh()
    stub = ISO.make_stub_call_participant(per_participant={
        "claude": "fine", "gemini": "fine",
        "flint": "Claude and Gemini both know I'm right about this.",
    })
    app.api_client.call_participant = stub
    r1 = _CLIENT.post("/api/chat", json={"message": "round one", "modes": ["blind"]})
    sid = r1.json()["session_id"]
    r2 = _CLIENT.post("/api/chat", json={"message": "round two", "session_id": sid,
                                         "modes": ["blind"]})
    assert r2.status_code == 409
    import session_manager, asyncio
    session = asyncio.run(session_manager.load_session(sid))
    assert len(session["rounds"]) == 1, "the refused round must not be saved"
    _cleanup()


def test_reveal_endpoint_flips_status_and_export_resolves():
    app = _fresh()
    stub = ISO.make_stub_call_participant(per_participant={
        "claude": "Short TTLs.", "gemini": "Aggressive caching.", "flint": "Depends.",
    })
    app.api_client.call_participant = stub
    r1 = _CLIENT.post("/api/chat", json={"message": "round one", "modes": ["blind"]})
    sid = r1.json()["session_id"]

    before = _CLIENT.get(f"/api/blind/{sid}").json()
    assert before["revealed"] is False and "mapping" not in before

    revealed = _CLIENT.post(f"/api/blind/{sid}/reveal").json()
    assert revealed["revealed"] is True
    assert set(revealed["mapping"].keys()) == {"claude", "gemini", "flint"}

    after = _CLIENT.get(f"/api/blind/{sid}").json()
    assert after["revealed"] is True

    exp = _CLIENT.post(f"/api/sessions/{sid}/export?format=html")
    assert exp.status_code == 200
    label = revealed["mapping"]["claude"]
    assert f"{label} — Claude" in exp.text
    _cleanup()


def test_reveal_unknown_session_404():
    app = _fresh()
    r = _CLIENT.post("/api/blind/no-such-session/reveal")
    assert r.status_code == 404
    _cleanup()


ALL_TESTS = [
    test_normal_mode_response_shape_unchanged,
    test_blind_two_rounds_six_prompts_zero_identity_leaks,
    test_blind_scoped_history_hides_round_one_identity_from_round_two_prompt,
    test_dirty_history_from_a_leaked_reply_fails_closed_next_round,
    test_roster_mismatch_fails_closed,
    test_failed_closed_round_is_never_persisted,
    test_reveal_endpoint_flips_status_and_export_resolves,
    test_reveal_unknown_session_404,
]

if __name__ == "__main__":
    for t in ALL_TESTS:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\nALL PASS ({len(ALL_TESTS)} tests)")
