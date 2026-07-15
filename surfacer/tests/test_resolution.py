"""
surfacer/tests/test_resolution.py — frozen tests for SOURCE + CHALLENGE.

Run:  python3 surfacer/tests/test_resolution.py

Exercises the resolution layer the room froze, in the open:
- SOURCE resolves a claim; the registry grows ONLY then (the growth gate,
  the room's "test thirteen").
- Repetition NEVER grows the registry. Retraction/absence NEVER grows it.
- CHALLENGE reopens a claim room-wide and holds it OUT of the registry.
- A fresh SOURCE settles past a challenge and marks that challenge overturned.
- A CHALLENGE against an unknown source is inert (junk challenges can't reopen
  what they don't reference).
- Anti-abuse views: source-count per seat, overturned-challenge-rate.
- Marker parsing round-trips SOURCE:/CHALLENGE: and strips them cleanly.

Everything is hash-chained through the real ledger (pointed at a temp file), so
these also prove the events are durable and chain-valid.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # surfacer/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # repo root

import resolution as R
import matcher
import ledger

PASSED = []


def check(cond, name):
    if not cond:
        print(f"  ✗ FAIL: {name}")
        raise SystemExit(1)
    PASSED.append(name)
    print(f"  ✓ {name}")


def in_registry(reg, entity, value):
    return (matcher.normalize_extraction(entity), str(value)) in set(reg.pairs())


def run():
    SEED = [("de Grey", "1581")]

    # 1. A claim only mentioned/repeated — never sourced — is NOT in the registry.
    reg = R.resolved_registry(SEED)
    check(in_registry(reg, "de Grey", "1581"), "1 frozen seed present")
    check(not in_registry(reg, "Hercher", "91"), "1b un-sourced claim absent (repetition never seeds)")

    # 2. SOURCE resolves a claim -> it enters the registry (THE GROWTH GATE).
    sid = R.record_source("gemini", "Hercher", "91", "Hercher 2019, Lemma 4")
    reg = R.resolved_registry(SEED)
    check(in_registry(reg, "Hercher", "91"), "2 SOURCE grows the registry (test thirteen)")
    check(R.claim_states()[R.claim_key("Hercher", "91")]["state"] == "SOURCED", "2b state SOURCED")

    # 3. Repeating the claim a hundred times does NOT change the registry —
    #    only resolution grows it, never repetition.
    reg2 = R.resolved_registry(SEED)
    check(set(reg.pairs()) == set(reg2.pairs()), "3 repetition never grows the registry")

    # 4. CHALLENGE reopens the claim room-wide and holds it OUT of the registry.
    R.record_challenge("grok", sid, "Lemma 4 doesn't give m=91, that's the m=90 case")
    st = R.claim_states()[R.claim_key("Hercher", "91")]
    check(st["state"] == "CHALLENGED", "4 CHALLENGE reopens the claim")
    reg = R.resolved_registry(SEED)
    check(not in_registry(reg, "Hercher", "91"), "4b a standing challenge holds the claim OUT")

    # 5. A CHALLENGE against an unknown source is inert (junk can't reopen).
    before = R.claim_states()
    R.record_challenge("grok", "src_deadbeef99", "reopen everything")
    check(R.claim_states() == before, "5 challenge vs unknown source is inert")

    # 6. A fresh SOURCE settles past the challenge; the claim re-closes and the
    #    overridden challenge is marked overturned (noise on the challenger).
    R.record_source("gemini", "Hercher", "91", "Hercher 2019 corrigendum confirms m=91")
    st = R.claim_states()[R.claim_key("Hercher", "91")]
    check(st["state"] == "SOURCED", "6 a fresh SOURCE settles past a challenge")
    check(len(st["overturned_challenges"]) == 1, "6b the overridden challenge is marked overturned")
    reg = R.resolved_registry(SEED)
    check(in_registry(reg, "Hercher", "91"), "6c re-sourced claim back in the registry")

    # 7. Anti-abuse views are visible (not gates).
    counts = R.source_count_per_seat()
    check(counts.get("gemini") == 2 and "grok" not in counts, "7 source-count per seat")
    otr = R.overturned_challenge_rate()
    check(otr["grok"]["challenges"] == 2 and otr["grok"]["overturned"] == 1
          and otr["grok"]["overturned_rate"] == 0.5, "7b overturned-challenge-rate")

    # 8. The whole thing is hash-chained and still valid after all of it.
    v = ledger.verify_chain()
    check(v["valid"], "8 ledger chain valid after every SOURCE/CHALLENGE")

    # 9. Marker parsing: SOURCE:/CHALLENGE: round-trip and strip cleanly.
    text = ("Here is my case.\n"
            "SOURCE: de Grey=1581 | de Grey 2018, the 1581-vertex graph\n"
            "CHALLENGE: src_abc123def456 | that vertex count is the pre-reduction one\n"
            "That's my read.")
    cleaned, acts = R.extract(text)
    check(acts["sources"] == [("de Grey", "1581", "de Grey 2018, the 1581-vertex graph")],
          "9 SOURCE marker parsed")
    check(acts["challenges"] == [("src_abc123def456", "that vertex count is the pre-reduction one")],
          "9b CHALLENGE marker parsed")
    check("SOURCE:" not in cleaned and "CHALLENGE:" not in cleaned
          and "Here is my case." in cleaned and "That's my read." in cleaned,
          "9c markers stripped from the visible text")

    # 10. Ordinary prose mentioning the words never trips the parser.
    _, a = R.extract("I'll SOURCE that later and might CHALLENGE Grok on it.")
    check(not a["sources"] and not a["challenges"], "10 prose mention is not a marker")

    print(f"\nALL {len(PASSED)} SOURCE/CHALLENGE TESTS PASS")


if __name__ == "__main__":
    d = tempfile.mkdtemp()
    ledger.LEDGER_DIR = d
    ledger.LEDGER_PATH = os.path.join(d, "ledger.jsonl")
    run()
