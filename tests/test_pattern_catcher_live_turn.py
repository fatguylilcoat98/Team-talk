"""Integration tests for the Pattern Catcher over the REAL /api/chat turn
path — office assignment, the LEDGER: marker convention, next-turn delivery,
and the read-only guarantee, exercised through app.chat() exactly as a
browser would call it (not by calling pattern_catcher.py's functions
directly — see test_ledger_query.py for the office/search unit coverage).

Standalone (no pytest): `python tests/test_pattern_catcher_live_turn.py`.
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


def _fresh():
    global _TMP, _CLIENT
    _TMP = tempfile.mkdtemp()
    ISO.redirect_all_stores(_TMP)
    import settings_store
    settings_store.get_participants = lambda: list(ISO.ROSTER)
    import app
    _CLIENT = TestClient(app.app)
    return app


def _cleanup():
    if _TMP and os.path.isdir(_TMP):
        shutil.rmtree(_TMP, ignore_errors=True)


# --------------------------------------------------------------------------

def test_no_office_holder_no_boot_block_and_query_refused():
    app = _fresh()
    stub = ISO.make_stub_call_participant(per_participant={
        "claude": "LEDGER: did anyone discuss caching?",
        "gemini": "no comment",
        "flint": "no comment",
    })
    app.api_client.call_participant = stub
    r = _CLIENT.post("/api/chat", json={"message": "go", "modes": ["collab"]})
    assert r.status_code == 200, r.text
    # No participant currently holds the office, so no one gets the boot block.
    for pid, system, prompt in stub.calls:
        assert "PATTERN CATCHER" not in prompt
    claude_resp = next(x for x in r.json()["responses"] if x["id"] == "claude")
    assert "LEDGER:" not in claude_resp["text"], "the marker must still be stripped from view"
    audit = _CLIENT.get("/api/pattern-catcher").json()
    assert audit["office"]["occupant"] is None
    assert audit["queries"][0]["refused"] is True
    _cleanup()


def test_holder_gets_capability_notice_and_result_arrives_next_turn():
    app = _fresh()
    import office_store
    office_store.assign(office_store.PATTERN_CATCHER, "flint", by="chris")

    stub = ISO.make_stub_call_participant(per_participant={
        "claude": "Caching should use short TTLs.",
        "gemini": "Agreed on short TTLs.",
        "flint": "LEDGER: caching",
    })
    app.api_client.call_participant = stub
    r1 = _CLIENT.post("/api/chat", json={"message": "thoughts on caching?", "modes": ["collab"]})
    assert r1.status_code == 200, r1.text
    sid = r1.json()["session_id"]

    # Round 1: FLINT holds the office, so the capability notice is present —
    # but the query was only just issued mid-round, so no RESULT is in this
    # turn's own prompt (that arrives next turn, same as mail/receipts).
    flint_call_1 = next(c for c in stub.calls if c[0] == "flint")
    assert "PATTERN CATCHER" in flint_call_1[2]
    assert "Query:" not in flint_call_1[2]
    flint_resp_1 = next(x for x in r1.json()["responses"] if x["id"] == "flint")
    assert "LEDGER:" not in flint_resp_1["text"]
    assert flint_resp_1.get("ledger_queried") == 1

    stub.calls.clear()
    stub2 = ISO.make_stub_call_participant(per_participant={
        "claude": "fine", "gemini": "fine", "flint": "fine",
    })
    app.api_client.call_participant = stub2
    r2 = _CLIENT.post("/api/chat", json={"message": "continue", "session_id": sid,
                                         "modes": ["collab"]})
    assert r2.status_code == 200, r2.text
    flint_call_2 = next(c for c in stub2.calls if c[0] == "flint")
    assert "PATTERN CATCHER" in flint_call_2[2]
    assert "Query: \"caching\"" in flint_call_2[2], "the staged result must arrive on the NEXT turn"
    assert "matched" in flint_call_2[2]

    # Delivered once — a third round must not repeat it.
    stub3 = ISO.make_stub_call_participant("fine")
    app.api_client.call_participant = stub3
    r3 = _CLIENT.post("/api/chat", json={"message": "again", "session_id": sid,
                                         "modes": ["collab"]})
    flint_call_3 = next(c for c in stub3.calls if c[0] == "flint")
    assert "Query: \"caching\"" not in flint_call_3[2]
    _cleanup()


def test_office_change_moves_the_capability_not_the_model():
    app = _fresh()
    import office_store
    office_store.assign(office_store.PATTERN_CATCHER, "flint", by="chris")
    office_store.assign(office_store.PATTERN_CATCHER, "gemini", by="chris")   # reassigned

    stub = ISO.make_stub_call_participant(per_participant={
        "claude": "no comment", "gemini": "no comment", "flint": "no comment",
    })
    app.api_client.call_participant = stub
    r = _CLIENT.post("/api/chat", json={"message": "go", "modes": ["collab"]})
    assert r.status_code == 200, r.text
    flint_call = next(c for c in stub.calls if c[0] == "flint")
    gemini_call = next(c for c in stub.calls if c[0] == "gemini")
    assert "PATTERN CATCHER" not in flint_call[2], "FLINT no longer holds the office"
    assert "PATTERN CATCHER" in gemini_call[2], "Gemini holds it now"
    _cleanup()


def test_query_is_read_only_no_session_or_ledger_content_mutated():
    app = _fresh()
    import office_store, ledger, session_manager, asyncio
    office_store.assign(office_store.PATTERN_CATCHER, "flint", by="chris")
    stub = ISO.make_stub_call_participant(per_participant={
        "claude": "fine", "gemini": "fine", "flint": "LEDGER: anything?",
    })
    app.api_client.call_participant = stub
    r = _CLIENT.post("/api/chat", json={"message": "go", "modes": ["collab"]})
    sid = r.json()["session_id"]
    before = asyncio.run(session_manager.load_session(sid))
    events_before = len(ledger._read_all())

    audit = _CLIENT.get("/api/pattern-catcher").json()
    assert audit["queries"][0]["result"]["ok"] is True

    after = asyncio.run(session_manager.load_session(sid))
    assert before == after, "reading the audit endpoint must not mutate the session"
    assert len(ledger._read_all()) == events_before, "reading the audit endpoint must not append to the ledger"
    _cleanup()


ALL_TESTS = [
    test_no_office_holder_no_boot_block_and_query_refused,
    test_holder_gets_capability_notice_and_result_arrives_next_turn,
    test_office_change_moves_the_capability_not_the_model,
    test_query_is_read_only_no_session_or_ledger_content_mutated,
]

if __name__ == "__main__":
    for t in ALL_TESTS:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\nALL PASS ({len(ALL_TESTS)} tests)")
