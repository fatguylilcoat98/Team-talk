"""Regression tests for two fixes made after the first live production
rehearsal:

1. SCOPING: blind turns used to include the shared, room-wide "memory_block"
   (long-term memory, notebook, wall, questions, workshop, proposals,
   studio, missions, CRT, code index, cross-session episodes, room-sense) —
   attributed content nobody had ever scoped for blind mode. It is now
   excluded (scoped out, never redacted) for blind turns only; normal mode
   is unaffected.

2. DELIVERY ORDERING: building a candidate blind prompt calls the same
   boot_block() functions normal mode does, which mark mail/receipts/
   scratch/code-requests/pattern-catcher pending items delivered as a side
   effect of construction — before the outbound audit runs. A snapshot/
   restore around the audited stage (app.py's _blind_snapshot/_blind_restore)
   now undoes that mutation when the audit finds a leak, so a failed-closed
   round never silently consumes one-time material.

Both fixes are entirely in app.py. identity_guard.py, blind_experiment.py,
and blind_context.py are asserted byte-unchanged by this file.

Standalone (no pytest): `python tests/test_blind_scoping_and_delivery.py`.
"""

import hashlib
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

import _test_isolation as ISO

_TMP = None
_CLIENT = None

# Captured from the working tree immediately before this session's changes —
# see the commit that adds this file for the exact baseline commit.
GOVERNANCE_HASHES = {
    "identity_guard.py": "e8505d1c1c575f9b1a9ff4d9adc004f2eb3236f108b681d0a3f048dc779e071e",
    "blind_experiment.py": "388ab39e8f0e346f3d4bb75adbabc9d9a2b6db78f1274f0eb88b4be97ea63560",
    "blind_context.py": "79e0b757564178fb5fb3e378b0deb334edf3adfe49ff52f43db163407acb3be1",
}
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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


def _sha256(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


# --------------------------------------------------------------------------
# 3. Governance files untouched (checked first — if this fails, nothing
#    else in this file should be trusted).
# --------------------------------------------------------------------------

def test_governance_files_are_byte_identical_to_baseline():
    for name, expected in GOVERNANCE_HASHES.items():
        actual = _sha256(os.path.join(REPO_ROOT, name))
        assert actual == expected, f"{name} changed — governance files must stay untouched ({actual})"


# --------------------------------------------------------------------------
# 1 & 2. Shared room-wide memory: excluded from blind, present in normal.
# --------------------------------------------------------------------------

def _seed_dirty_shared_content():
    import notebook_store, crt_store
    notebook_store.add_entry("Claude's point about TTLs was the strongest one in the room.", "Gemini")
    crt_store.pin("Grok's early framing didn't survive the round.", "FLINT")


def test_dirty_shared_room_memory_excluded_from_blind_prompts():
    app = _fresh()
    _seed_dirty_shared_content()
    stub = ISO.make_stub_call_participant(per_participant={
        "claude": "Short TTLs are safer.", "gemini": "Aggressive caching wins on speed.",
        "flint": "Depends on read/write ratio.",
    })
    app.api_client.call_participant = stub
    r = _CLIENT.post("/api/chat", json={"message": "thoughts on caching TTLs?", "modes": ["blind"]})
    assert r.status_code == 200, r.text   # would 409 if the dirty content leaked through
    for pid, system, prompt in stub.calls:
        full = f"{system}\n\n{prompt}"
        assert "Claude's point about TTLs" not in full, (pid, "notebook leaked")
        assert "Grok's early framing" not in full, (pid, "CRT leaked")
    _cleanup()


def test_same_shared_room_memory_present_in_normal_prompts():
    app = _fresh()
    _seed_dirty_shared_content()
    stub = ISO.make_stub_call_participant("noted")
    app.api_client.call_participant = stub
    r = _CLIENT.post("/api/chat", json={"message": "thoughts?", "modes": ["collab"]})
    assert r.status_code == 200, r.text
    found_notebook = any("Claude's point about TTLs" in f"{s}\n\n{p}" for _, s, p in stub.calls)
    found_crt = any("Grok's early framing" in f"{s}\n\n{p}" for _, s, p in stub.calls)
    assert found_notebook, "notebook content must still reach normal-mode prompts"
    assert found_crt, "CRT content must still reach normal-mode prompts"
    _cleanup()


# --------------------------------------------------------------------------
# 4 & 5. Delivery-state snapshot/restore around the outbound audit.
# --------------------------------------------------------------------------

def test_failed_blind_audit_causes_no_delivery_side_effects():
    app = _fresh()
    import mailbox_store
    # Dirty mail: the message body itself names another participant, so
    # flint's boot block (built while constructing the candidate prompt)
    # will carry the leak straight into the outbound audit.
    item = mailbox_store.send("Chris", "flint", "FLINT",
                              "Don't trust what Gemini said about caching.")
    assert item["delivered_at"] is None

    stub = ISO.make_stub_call_participant(per_participant={
        "claude": "Short TTLs.", "gemini": "Aggressive caching.", "flint": "Depends.",
    })
    app.api_client.call_participant = stub
    r = _CLIENT.post("/api/chat", json={"message": "go", "modes": ["blind"]})
    assert r.status_code == 409, r.text
    assert not stub.calls, "no participant should have been called"

    mail_after = [m for m in mailbox_store.list_mail() if m["id"] == item["id"]][0]
    assert mail_after["delivered_at"] is None, \
        "a failed-closed round must not consume one-time mail"
    _cleanup()


def test_successful_blind_audit_commits_delivery_exactly_once():
    app = _fresh()
    import mailbox_store
    # Clean mail: no other participant is named, so nothing trips the audit.
    item = mailbox_store.send("Chris", "flint", "FLINT",
                              "Welcome to the room.")
    assert item["delivered_at"] is None

    stub = ISO.make_stub_call_participant(per_participant={
        "claude": "Short TTLs.", "gemini": "Aggressive caching.", "flint": "Depends.",
    })
    app.api_client.call_participant = stub
    r = _CLIENT.post("/api/chat", json={"message": "go", "modes": ["blind"]})
    assert r.status_code == 200, r.text

    # flint's prompt actually carried the mail — proves this was a real
    # delivery, not a lucky no-op.
    flint_call = next(c for c in stub.calls if c[0] == "flint")
    assert "Welcome to the room." in flint_call[2]

    mail_after = [m for m in mailbox_store.list_mail() if m["id"] == item["id"]][0]
    assert mail_after["delivered_at"] is not None
    first_delivered_at = mail_after["delivered_at"]

    # A second round must not re-deliver it (exactly once, not "at least once").
    stub2 = ISO.make_stub_call_participant("fine")
    app.api_client.call_participant = stub2
    sid = r.json()["session_id"]
    r2 = _CLIENT.post("/api/chat", json={"message": "again", "session_id": sid, "modes": ["blind"]})
    assert r2.status_code == 200, r2.text
    flint_call_2 = next(c for c in stub2.calls if c[0] == "flint")
    assert "Welcome to the room." not in flint_call_2[2]
    mail_final = [m for m in mailbox_store.list_mail() if m["id"] == item["id"]][0]
    assert mail_final["delivered_at"] == first_delivered_at
    _cleanup()


ALL_TESTS = [
    test_governance_files_are_byte_identical_to_baseline,
    test_dirty_shared_room_memory_excluded_from_blind_prompts,
    test_same_shared_room_memory_present_in_normal_prompts,
    test_failed_blind_audit_causes_no_delivery_side_effects,
    test_successful_blind_audit_commits_delivery_exactly_once,
]

if __name__ == "__main__":
    for t in ALL_TESTS:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\nALL PASS ({len(ALL_TESTS)} tests)")
