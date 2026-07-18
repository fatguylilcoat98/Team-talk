"""Tests for the bounded, read-only reasoning-ledger snapshot feeding the passes.

Proves the snapshot is bounded, read-only, seat-safe, secret-free, and carries
no hidden chain-of-thought. Standalone: `python tests/test_reflection_snapshot.py`.
"""

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import reasoning_store as RS
import ledger
from reflection import snapshot


def _fresh(n):
    d = tempfile.mkdtemp()
    RS.CLAIMS_PATH = os.path.join(d, "c.jsonl")
    RS.PARTICIPATIONS_PATH = os.path.join(d, "p.jsonl")
    ledger.LEDGER_DIR = d
    ledger.LEDGER_PATH = os.path.join(d, "ledger.jsonl")
    claim, _ = RS.open_claim("Chris", "root goal")
    for i in range(n):
        seat = ["claude", "gemini", "flint"][i % 3]
        RS.append_participation(claim["claim_id"], seat,
                                f"participation {i}: seat {seat} did work item number {i} " + ("x" * 400))
    return d


def test_snapshot_bounded():
    d = _fresh(40)                      # more than MAX_RECEIPTS
    try:
        snap = snapshot.ledger_snapshot()
        assert len(snap["receipts"]) <= snapshot.MAX_RECEIPTS
        assert all(len(r["text"]) <= snapshot.EXCERPT + 1 for r in snap["receipts"])
        assert len(snap["attribution_map"]) <= snapshot.MAX_RECEIPTS
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_snapshot_read_only():
    d = _fresh(10)
    try:
        before = (os.path.getsize(RS.PARTICIPATIONS_PATH), os.path.getmtime(RS.PARTICIPATIONS_PATH))
        snapshot.ledger_snapshot()
        snapshot.ledger_snapshot()
        after = (os.path.getsize(RS.PARTICIPATIONS_PATH), os.path.getmtime(RS.PARTICIPATIONS_PATH))
        assert before == after                          # ledger file untouched
        # module exposes no writer to the ledger
        assert not hasattr(snapshot, "append") and not hasattr(snapshot, "record")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_snapshot_seat_safe_and_shape():
    d = _fresh(6)
    try:
        snap = snapshot.ledger_snapshot()
        for r in snap["receipts"]:
            assert set(r.keys()) == {"id", "text"}      # nothing but id + bounded excerpt
        for phrase, seat in snap["attribution_map"].items():
            assert seat in ("Chris", "claude", "gemini", "flint")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_snapshot_no_secrets_no_cot():
    d = _fresh(6)
    try:
        blob = json.dumps(snapshot.ledger_snapshot()).lower()
        for banned in ("api_key", "sk-ant", "sk-", "authorization", "bearer",
                       "password", "system prompt", "chain-of-thought", "reasoning:"):
            assert banned not in blob
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_snapshot_degrades_safely():
    # point at a nonexistent store -> empty snapshot, no raise
    RS.PARTICIPATIONS_PATH = os.path.join(tempfile.gettempdir(), "does_not_exist_xyz.jsonl")
    snap = snapshot.ledger_snapshot()
    assert snap == {"receipts": [], "attribution_map": {}}


ALL_TESTS = [test_snapshot_bounded, test_snapshot_read_only, test_snapshot_seat_safe_and_shape,
             test_snapshot_no_secrets_no_cot, test_snapshot_degrades_safely]

if __name__ == "__main__":
    for t in ALL_TESTS:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\nALL PASS ({len(ALL_TESTS)} tests)")
