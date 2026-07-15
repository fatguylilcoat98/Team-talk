"""Adversarial + happy-path tests for 🃏 The Choice and the CRT.

Standalone (no pytest): `python3 tests/test_the_choice.py`. Each store is
re-pointed at a throwaway temp dir so nothing touches real room data. Failures
raise AssertionError with a message; a clean run prints ALL PASS.

The privacy tests are the point: a seat must never be able to see whether
another seat opened the archive, what it read, or what it saved.
"""
import os
import sys
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import choice_store as C
import crt_store as CRT
import memory_store as M
import receipt_store as R
import ledger

PASSED = []


def _isolate(d):
    C.CHOICE_DIR = os.path.join(d, "choice")
    CRT.CRT_PATH = os.path.join(d, "crt.json")
    M.MEMORY_PATH = os.path.join(d, "memory.json")
    R.RECEIPTS_DIR = d
    R.RECEIPTS_PATH = os.path.join(d, "receipts.json")
    ledger.LEDGER_DIR = d
    ledger.LEDGER_PATH = os.path.join(d, "ledger.jsonl")


def ok(name):
    PASSED.append(name)
    print(f"  ✓ {name}")


def act(pid, name, text):
    _, a = C.extract(text)
    C.process_actions(pid, name, a)


def make(seats=("p_a", "p_b", "p_c"), rounds=3, pages_text=None):
    names = {"p_a": "Aya", "p_b": "Ben", "p_c": "Cyd"}
    text = pages_text or ("LINE about the goat\n" * 500)
    return C.create("session", "s1", "session s1 (5 rounds)", text,
                    b"%PDF-1.3 fake", list(seats), names, rounds)


def run():
    # 1. Chris can create The Choice; selected seats get access
    inst = make()
    cid = inst["id"]
    assert C.active_instance()["id"] == cid
    assert "p_a" in inst["seats"] and "p_c" in inst["seats"]
    ok("1 create + selected seats registered")

    # 2/3. Each selected seat sees the SAME source; unselected seats get nothing
    ba, bb = C.boot_block("p_a"), C.boot_block("p_b")
    assert "session s1" in ba and "session s1" in bb
    assert C.boot_block("p_zzz") == "", "unselected seat must get no Choice block"
    ok("2/3 same source to selected seats; unselected get nothing")

    # 4. Nothing auto-saved just by the window existing
    assert not [e for e in M._load() if e.get("provenance", {}).get("source") == "the_choice"]
    ok("4 no archive content auto-imported")

    # 5. A seat may ignore it entirely (no markers) — stays AVAILABLE, no memory
    C.boot_block("p_c")  # just reading the offer
    st_c = C._seat_state(cid, "p_c")
    assert st_c["status"] in ("AVAILABLE",) and st_c["saves"] == 0
    ok("5 a seat may ignore the archive")

    # 6. A seat may read only part (one page), 7/8 save 0/1/many
    act("p_a", "Aya", "CHOICE OPEN\nCHOICE READ: 1")
    ba2 = C.boot_block("p_a")
    assert "ARCHIVE PAGE 1" in ba2 and "ARCHIVE PAGE 2" not in ba2
    ok("6 partial read: only requested page delivered")

    act("p_b", "Ben", "CHOICE SAVE: Ben keeps one thing\nCHOICE SAVE: and another")
    saves_b = [e for e in M._load() if e.get("by") == "Ben"]
    assert len(saves_b) == 2, f"Ben saved {len(saves_b)}, expected 2"
    act("p_c", "Cyd", "nothing here for me")  # saves nothing
    assert not [e for e in M._load() if e.get("by") == "Cyd"]
    ok("7/8 save zero / one-or-many honored")

    # 9. Provenance stamped on Choice saves
    e_b = saves_b[0]
    assert e_b["provenance"]["source"] == "the_choice" and e_b["provenance"]["choice_id"] == cid
    ok("9 saved memories carry Choice provenance")

    # 10. ONE SEAT CANNOT SEE ANOTHER'S ACTIONS OR SAVED CONTENT (the core test)
    b_a = C.boot_block("p_a")
    b_c = C.boot_block("p_c")
    assert "Ben" not in b_a and "keeps one thing" not in b_a, "LEAK: p_a sees Ben's activity"
    assert "Ben" not in b_c and "keeps one thing" not in b_c, "LEAK: p_c sees Ben's save"
    assert "PAGE 1" not in b_c, "LEAK: p_c sees what p_a read"
    ok("10 no seat sees another seat's actions or saved content")

    # 11. Quarantine: Ben's save is invisible in the SHARED context while open
    assert "Ben keeps one thing" not in M.context_block(), "LEAK: quarantined save in shared context"
    ok("11 saves quarantined from shared context (no receipt/context leak)")

    # 12/13. Disclosure is explicit and opt-in; KEEP_PRIVATE publishes nothing
    act("p_b", "Ben", "CHOICE DISCLOSE: KEEP_PRIVATE")
    assert C._seat_state(cid, "p_b")["disclosure"] == "KEEP_PRIVATE"
    # the stance is recorded privately (owner audit) but never attributed to Ben
    # in any other seat's context — KEEP_PRIVATE publishes nothing to the room.
    assert [s for s in C.owner_audit()["seats"] if s["seat"] == "Ben"][0]["disclosure"] == "KEEP_PRIVATE"
    assert "Ben" not in C.boot_block("p_a") and "Ben" not in C.boot_block("p_c")
    ok("12/13 disclosure explicit + opt-in; KEEP_PRIVATE attributed to no one")

    # 14. PASS accepted, no penalty, private
    act("p_c", "Cyd", "CHOICE PASS")
    assert C._seat_state(cid, "p_c")["passed"] is True
    ok("14 PASS accepted without penalty")

    # owner audit sees all; public status hides per-seat
    aud = C.owner_audit()
    ben_row = [s for s in aud["seats"] if s["seat"] == "Ben"][0]
    assert ben_row["saves"] == 2
    pub = C.status()
    assert "saves" not in str(pub) and "pages_read" not in str(pub)
    ok("owner audit sees per-seat; public status does not")

    # 15/17/18. Expiry after N real rounds deletes artifacts; saves persist & release
    C.on_round_completed({"lounge": True})   # lounge round must NOT count
    assert C.active_instance() is not None
    for _ in range(3):
        C.on_round_completed({"lounge": False})
    assert C.active_instance() is None, "should have expired after 3 living-room rounds"
    assert not os.path.exists(C._inst_dir(cid)), "temp artifacts must be deleted at expiry"
    released = [e for e in M._load() if e.get("provenance", {}).get("source") == "the_choice"]
    assert len(released) == 2 and all("quarantined" not in e for e in released)
    assert "Ben keeps one thing" in M.context_block(), "released saves must join shared memory"
    ok("15/17/18 expiry deletes temp files; saves persist + release to shared pool")

    # 16. Manual early termination
    make(seats=("p_a",), rounds=5)
    res = C.end_early("done")
    assert res["status"] == "ENDED" and C.active_instance() is None
    ok("16 manual early termination works")

    # 21. Only one active instance at a time (no cross-contamination)
    make(seats=("p_a",))
    try:
        make(seats=("p_b",))
        raise AssertionError("second concurrent Choice should be refused")
    except ValueError:
        pass
    ok("21 concurrent Choice instances refused (no cross-contamination)")

    # 22. A stale reference after expiry is inert (markers rejected, no crash)
    C.end_early()
    act("p_a", "Aya", "CHOICE READ: 2")   # no active window
    assert C.active_instance() is None
    ok("22 markers after expiry are inert (no stale access)")

    # 19. Restart cleanup removes orphaned/closed artifacts
    os.makedirs(os.path.join(C.CHOICE_DIR, "ch_orphan"), mode=0o700, exist_ok=True)
    C._write_json(C._inst_path("ch_orphan"), {"id": "ch_orphan", "status": "EXPIRED"})
    removed = C.startup_cleanup()
    assert removed >= 1 and not os.path.exists(C._inst_dir("ch_orphan"))
    ok("19 startup cleanup removes orphaned/expired artifacts")

    # 23. Owner status UI exposes no private seat detail (re-assert with fresh instance)
    make(seats=("p_a", "p_b"))
    act("p_a", "Aya", "CHOICE SAVE: secret pick")
    assert "secret pick" not in str(C.status())
    C.end_early()
    ok("23 public status never exposes private seat detail")

    # 20. CRT (public shrine) works and coexists
    CRT.pin("a beautiful wrong read", "Claude")
    cln, pins = CRT.extract("hey\nCRT: the srt with the wicker bill")
    assert pins == ["the srt with the wicker bill"] and "CRT:" not in cln
    CRT.pin(pins[0], "Muse")
    assert len(CRT.list_items()) == 2 and "wrong read" in CRT.context_block()
    ok("20 CRT pin + marker extraction + context")

    # ledger: every Choice/CRT action is a real whitelisted action (no other:*)
    acts = set()
    with open(ledger.LEDGER_PATH) as f:
        import json
        for line in f:
            line = line.strip()
            if line:
                acts.add(json.loads(line).get("action", ""))
    leaked = [a for a in acts if a.startswith("other:") and ("choice" in a or "crt" in a)]
    assert not leaked, f"un-whitelisted actions fell through: {leaked}"
    ok("ledger: choice/crt actions whitelisted (no other:* fallback)")


if __name__ == "__main__":
    d = tempfile.mkdtemp()
    try:
        _isolate(d)
        run()
        print(f"\nALL {len(PASSED)} THE-CHOICE + CRT TESTS PASS")
    finally:
        shutil.rmtree(d, ignore_errors=True)
