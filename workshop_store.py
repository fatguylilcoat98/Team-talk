"""🔨 The Workshop — the gatekept workbench.

The one capability the room asked for by consensus: a place to work
between Chris's messages, where every edit is ledgered and being wrong
costs the seat that wrote it something visible and structural.

- ONE active target at a time: a goal, one artifact file, and a judge.
- The judge is code (a Chris-authored check script) or Chris himself
  ("manual" mode). Never a vote. Eloquence scores nothing here.
- Artifact versions are hash-chained, append-only. A reverted edit
  stays on the chain marked failed — the scar is structural.
- A seat whose edit fails the check is write-locked for the next
  cycle. The lock is public: the room sees it, the ledger records it,
  the seat's receipt says ✗ REJECTED with the actual test output.

Storage: workshop/state.json + workshop/versions/v{n}.txt (content)
+ workshop/versions.jsonl (the hash chain). All gitignored.
"""

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import List, Optional

import ledger

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSHOP_DIR = os.path.join(BASE_DIR, "workshop")
STATE_PATH = os.path.join(WORKSHOP_DIR, "state.json")
VERSIONS_DIR = os.path.join(WORKSHOP_DIR, "versions")
CHAIN_PATH = os.path.join(WORKSHOP_DIR, "versions.jsonl")
CHECK_PATH = os.path.join(WORKSHOP_DIR, "check.py")
ARTIFACT_DIR = os.path.join(WORKSHOP_DIR, "current")

GENESIS = "0" * 64
MAX_ARTIFACT_BYTES = 60_000
LOCK_CYCLES = 1          # cycles a seat sits out after a failed check


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _ensure_dirs() -> None:
    os.makedirs(VERSIONS_DIR, mode=0o700, exist_ok=True)
    os.makedirs(ARTIFACT_DIR, mode=0o700, exist_ok=True)


def load_state() -> dict:
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"target": None, "locks": {}, "cycles": 0, "auto_cycle": True}


def save_state(state: dict) -> None:
    _ensure_dirs()
    tmp = f"{STATE_PATH}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    os.replace(tmp, STATE_PATH)


# --- target lifecycle -------------------------------------------------------

def set_target(goal: str, filename: str, content: str,
               check_mode: str = "manual", check_script: str = "") -> Optional[dict]:
    """Open a new target. Only Chris calls this. Starts the version chain
    at v1 with his seed content, authored by Chris, auto-passing."""
    goal = (goal or "").strip()[:2000]
    filename = os.path.basename((filename or "artifact.txt").strip())[:80] or "artifact.txt"
    if not goal or len(content.encode("utf-8")) > MAX_ARTIFACT_BYTES:
        return None
    check_mode = check_mode if check_mode in ("script", "manual") else "manual"
    _ensure_dirs()
    _archive_previous_target()
    state = load_state()
    state["target"] = {
        "goal": goal,
        "filename": filename,
        "check_mode": check_mode,
        "status": "active",
        "set_at": _now(),
    }
    state["locks"] = {}
    state["cycles"] = 0
    if check_mode == "script":
        with open(CHECK_PATH, "w", encoding="utf-8") as f:
            f.write(check_script or "")
    save_state(state)
    append_version(content, "Chris", note="target opened",
                   check={"status": "seed", "output": ""})
    return state["target"]


def ship_target() -> Optional[dict]:
    state = load_state()
    if not state.get("target") or state["target"]["status"] != "active":
        return None
    state["target"]["status"] = "shipped"
    state["target"]["shipped_at"] = _now()
    save_state(state)
    return state["target"]


def _archive_previous_target() -> None:
    """A new target gets a fresh chain. The old one is never deleted —
    it moves whole (chain + every version file) into workshop/archive/,
    where it stays verifiable forever."""
    if not os.path.exists(CHAIN_PATH):
        return
    import shutil
    stamp = _now().replace(":", "-")
    dest = os.path.join(WORKSHOP_DIR, "archive", stamp)
    os.makedirs(dest, mode=0o700, exist_ok=True)
    shutil.move(CHAIN_PATH, os.path.join(dest, "versions.jsonl"))
    if os.path.isdir(VERSIONS_DIR):
        shutil.move(VERSIONS_DIR, os.path.join(dest, "versions"))
    _ensure_dirs()


# --- the version chain ------------------------------------------------------

def _read_chain() -> List[dict]:
    entries = []
    try:
        with open(CHAIN_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        entries.append({"_corrupt": True})
    except OSError:
        pass
    return entries


def version_hash(v: int, ts: str, by: str, content_hash: str,
                 status: str, prev_hash: str) -> str:
    return _sha(f"{v}|{ts}|{by}|{content_hash}|{status}|{prev_hash}")


def append_version(content: str, by: str, note: str = "",
                   check: Optional[dict] = None) -> dict:
    """Every submitted edit lands on the chain — including failed ones.
    A failed version's content is preserved but the live artifact stays
    at the last passing version. The scar is permanent by design.

    Chain math MUST ignore verdict rows (Chris's manual rulings ride
    outside the content chain with no hash): the room caught the bug
    where a ruling row's empty hash became the next edit's prev_hash —
    breaking the chain — and ruling an older version could even reuse
    a version number and overwrite its file. Real versions only, here."""
    _ensure_dirs()
    chain = [e for e in _read_chain()
             if not e.get("verdict_for") and not e.get("reanchor_for")
             and not e.get("_corrupt")]
    v = (max(e.get("v", 0) for e in chain) + 1) if chain else 1
    prev = chain[-1]["hash"] if chain else GENESIS
    ts = _now()
    check = check or {"status": "pending", "output": ""}
    content_hash = _sha(content)
    entry = {
        "v": v, "ts": ts, "by": str(by)[:60],
        "note": (note or "")[:200],
        "content_hash": content_hash,
        "check": {"status": str(check.get("status", ""))[:20],
                  "output": str(check.get("output", ""))[:1500]},
        "prev_hash": prev,
        "hash": version_hash(v, ts, str(by)[:60], content_hash,
                             str(check.get("status", ""))[:20], prev),
    }
    with open(os.path.join(VERSIONS_DIR, f"v{v}.txt"), "w", encoding="utf-8") as f:
        f.write(content)
    with open(CHAIN_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def update_check(v: int, status: str, output: str) -> None:
    """Manual-judge flow: Chris rules on version v after the fact. The
    original row is never rewritten — his verdict lands as a separate
    appended row (verdict_for=v) that rides outside the content chain."""
    chain = _read_chain()
    if not any(e.get("v") == v for e in chain):
        return
    # A later verdict on the same version silently superseded an earlier one in
    # latest_passing() (last-in-file-order wins). Re-ruling is legitimate, but
    # it must be on the record — otherwise a "passed" can quietly become
    # "failed" with no trace, the same silent-eviction hole as the memory cap.
    prior = [e for e in chain if e.get("verdict_for") == v]
    if prior:
        old_status = (prior[-1].get("check") or {}).get("status")
        if old_status != str(status)[:20]:
            ledger.append("Chris", "workshop_verdict_overridden", ref=f"v{v}",
                          detail={"from": old_status, "to": str(status)[:20],
                                  "note": "a later verdict supersedes the earlier one on this version"})
    entry = {
        "v": v, "ts": _now(), "by": "Chris",
        "verdict_for": v,
        "check": {"status": str(status)[:20], "output": str(output)[:1500]},
        "hash": "",  # verdict rows ride outside the content chain
    }
    with open(CHAIN_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def list_versions(limit: int = 50) -> List[dict]:
    return _read_chain()[-max(1, min(limit, 500)):]


def read_version(v: int) -> Optional[str]:
    try:
        with open(os.path.join(VERSIONS_DIR, f"v{int(v)}.txt"), "r", encoding="utf-8") as f:
            return f.read()
    except (OSError, ValueError):
        return None


def latest_passing() -> Optional[dict]:
    """The live artifact = newest version whose check passed (or seed)."""
    verdicts = {}
    for e in _read_chain():
        if e.get("verdict_for"):
            verdicts[e["verdict_for"]] = e["check"]["status"]
    best = None
    for e in _read_chain():
        if e.get("_corrupt") or e.get("verdict_for") or e.get("reanchor_for"):
            continue
        status = verdicts.get(e["v"], e["check"]["status"])
        if status in ("passed", "seed"):
            best = e
    return best


def verify_chain() -> dict:
    all_rows = _read_chain()
    # Accepted re-anchors: an authored, append-only acceptance of a KNOWN break,
    # pinned to that version's exact hash. It forgives a broken LINK (a scar we
    # can't rewrite) but never a tampered ROW — if the version's own content is
    # altered, its hash changes, the pin no longer matches, and the break is back.
    reanchors = {e["reanchor_for"]: e.get("accepted_hash")
                 for e in all_rows if e.get("reanchor_for")}
    entries = [e for e in all_rows
               if not e.get("verdict_for") and not e.get("reanchor_for")]
    prev = GENESIS
    for e in entries:
        if e.get("_corrupt"):
            return {"valid": False, "length": len(entries), "first_bad": None,
                    "reanchored": sorted(reanchors)}
        v = e.get("v")
        # The chain protects row METADATA, but the thing the Workshop gatekeeps
        # is the artifact CONTENT. Re-hash the actual v{n}.txt on disk and hold
        # it against the row's content_hash — otherwise the file can be swapped
        # under a still-"valid" chain. Not re-anchorable: it's altered content.
        disk = read_version(v)
        if disk is None:
            return {"valid": False, "length": len(entries), "first_bad": v,
                    "reason": "artifact file missing", "reanchored": sorted(reanchors)}
        if _sha(disk) != e.get("content_hash"):
            return {"valid": False, "length": len(entries), "first_bad": v,
                    "reason": "artifact content altered", "reanchored": sorted(reanchors)}
        expected = version_hash(v or 0, e.get("ts", ""), e.get("by", ""),
                                e.get("content_hash", ""),
                                e.get("check", {}).get("status", ""), e.get("prev_hash", ""))
        if e.get("hash") != expected:
            # The row hashes its own fields wrong — tampered content, not a mere
            # broken link. A re-anchor cannot forgive this.
            return {"valid": False, "length": len(entries), "first_bad": v,
                    "reason": "row hash mismatch", "reanchored": sorted(reanchors)}
        if e.get("prev_hash") != prev:
            if reanchors.get(v) == e.get("hash"):
                prev = e["hash"]     # accepted re-root from this version onward
                continue
            # Distinguish the genuine historical scar from laundered tampering.
            # A DANGLING row (empty/GENESIS prev_hash — no real parent recorded,
            # like the verdict-row bug that first broke the chain) is the real
            # break, and re-anchorable. But a prev_hash that NAMES a real parent
            # which no longer matches the actual predecessor means an UPSTREAM
            # content row was rewritten (its own hash recomputed to stay
            # self-consistent, pushing the break one row down). Re-anchoring here
            # would forgive that tamper — so it is NOT re-anchorable.
            ph = e.get("prev_hash") or ""
            if ph and ph != GENESIS:
                return {"valid": False, "length": len(entries), "first_bad": v,
                        "reason": "upstream row altered", "reanchored": sorted(reanchors)}
            return {"valid": False, "length": len(entries), "first_bad": v,
                    "reason": "broken chain link", "reanchored": sorted(reanchors)}
        prev = e["hash"]
    return {"valid": True, "length": len(entries), "first_bad": None,
            "reanchored": sorted(reanchors)}


def reanchor(v: int, reason: str, authority: str = "Chris") -> Optional[dict]:
    """Accept a known chain break at version v. Append-only: a re-anchor row
    rides outside the content chain (like a verdict row) and pins to v's CURRENT
    hash. v's own row is never rewritten — the scar stays permanently visible;
    the re-anchor just records that it is known, accepted, and by whom. Returns
    None if v isn't a real content version."""
    target = next((e for e in _read_chain()
                   if e.get("v") == v and not e.get("verdict_for")
                   and not e.get("reanchor_for")), None)
    if not target:
        return None
    row = {
        "reanchor_for": v,
        "ts": _now(),
        "by": str(authority)[:60],
        "reason": str(reason)[:300],
        "accepted_hash": target.get("hash"),
        "hash": "",   # rides outside the content chain, like verdict rows
    }
    with open(CHAIN_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


# --- locks ------------------------------------------------------------------

def lock_seat(state: dict, seat_id: str) -> None:
    state["locks"][seat_id] = LOCK_CYCLES + 1  # decremented at cycle start


def is_locked(state: dict, seat_id: str) -> bool:
    return state.get("locks", {}).get(seat_id, 0) > 0


def tick_locks(state: dict) -> None:
    locks = state.get("locks", {})
    for k in list(locks):
        locks[k] -= 1
        if locks[k] <= 0:
            del locks[k]


# --- context for the room ---------------------------------------------------

def context_block(participants: Optional[List[dict]] = None) -> str:
    state = load_state()
    target = state.get("target")
    if not target or target.get("status") != "active":
        return ""
    live = latest_passing()
    chain = verify_chain()
    names = {p["id"]: p["name"] for p in (participants or [])}
    locked = [names.get(k, k) for k, v in state.get("locks", {}).items() if v > 0]
    lines = [
        "=== 🔨 WORKSHOP — ACTIVE TARGET ===",
        f"Goal: {target['goal']}",
        f"Artifact: {target['filename']} · live version v{live['v'] if live else 0} · "
        f"chain {'valid' if chain['valid'] else 'BROKEN'} · {state.get('cycles', 0)} work cycles run",
        f"Judge: {'check script (code decides)' if target['check_mode'] == 'script' else 'Chris rules manually'}",
    ]
    if locked:
        lines.append(f"Write-locked this cycle (failed the check last cycle): {', '.join(locked)}")
    lines.append("Work happens at the bench between rounds, not in chat. A failing "
                 "edit is reverted, receipted ✗ REJECTED, and costs the seat a cycle. "
                 "Discuss freely here; only the bench changes the artifact.")
    return "\n".join(lines)
