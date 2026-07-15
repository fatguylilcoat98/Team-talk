"""Corrupt-session quarantine — the "eviction ghost" the room flagged.

A truncated/corrupt session file used to load as None, identical to "not found".
The chat endpoint then treats that id as a NEW session and the next save
os.replace()s the corrupt-but-recoverable archive with an empty one — the whole
conversation vanishes with no tombstone. The loader now sets a bad file aside
(a name that isn't listed or reloaded) and ledgers it, so the next save writes
a fresh file instead of clobbering the archive.

Standalone runner: `python3 tests/test_session_quarantine.py`.
"""

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import session_manager as sm
import ledger

_passed = 0


def check(cond, label):
    global _passed
    if not cond:
        print(f"  ✗ FAIL: {label}")
        raise SystemExit(1)
    _passed += 1
    print(f"  ✓ {label}")


async def main():
    d = tempfile.mkdtemp()
    sm.SESSIONS_DIR = os.path.join(d, "sessions")
    os.makedirs(sm.SESSIONS_DIR)
    ledger.LEDGER_PATH = os.path.join(d, "ledger.jsonl")

    sid = "team-talk-cafef00d"
    path = sm._path(sid)

    # A well-formed session loads normally and is untouched.
    good = sm.new_session(sid)
    good["rounds"].append({"round": 1, "chris_message": "hi", "responses": []})
    await sm.save_session(good)
    loaded = await sm.load_session(sid)
    check(loaded is not None and len(loaded["rounds"]) == 1, "valid session loads")
    check(os.path.exists(path), "valid session file left in place")

    # Now corrupt it (simulate a crash mid-write on an old code path).
    with open(path, "w", encoding="utf-8") as f:
        f.write('{"id": "team-talk-cafef00d", "rounds": [ {trunca')

    res = await sm.load_session(sid)
    check(res is None, "corrupt file degrades to not-found (no 500)")
    check(not os.path.exists(path), "corrupt file moved OFF the live path")

    corrupts = [n for n in os.listdir(sm.SESSIONS_DIR) if n.endswith(".corrupt")]
    check(len(corrupts) == 1, "exactly one quarantined artifact")
    check(not corrupts[0].endswith(".json"), "quarantine name won't be re-listed/reloaded")

    with open(os.path.join(sm.SESSIONS_DIR, corrupts[0]), encoding="utf-8") as f:
        check("cafef00d" in f.read(), "corrupt bytes preserved for recovery")

    evs = [e for e in ledger._read_all() if e.get("action") == "session_quarantined"]
    check(len(evs) == 1 and evs[0]["ref"] == sid, "quarantine recorded on the ledger")

    # THE POINT: a fresh save under the same id must NOT destroy the archive.
    fresh = sm.new_session(sid)
    await sm.save_session(fresh)
    check(os.path.exists(os.path.join(sm.SESSIONS_DIR, corrupts[0])),
          "fresh save does not clobber the quarantined archive")
    reloaded = await sm.load_session(sid)
    check(reloaded is not None and reloaded["rounds"] == [], "id is usable again after quarantine")

    # list_sessions must skip the quarantined artifact silently.
    summaries = await sm.list_sessions()
    check(all(not s["id"].endswith(".corrupt") for s in summaries),
          "quarantined artifact absent from the session list")

    print(f"\nALL {_passed} SESSION-QUARANTINE TESTS PASS")


if __name__ == "__main__":
    asyncio.run(main())
