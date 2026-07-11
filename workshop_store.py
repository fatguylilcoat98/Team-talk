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
    at the last passing version. The scar is permanent by design."""
    _ensure_dirs()
    chain = _read_chain()
    v = (chain[-1]["v"] + 1) if chain else 1
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
        if e.get("_corrupt") or e.get("verdict_for"):
            continue
        status = verdicts.get(e["v"], e["check"]["status"])
        if status in ("passed", "seed"):
            best = e
    return best


def verify_chain() -> dict:
    prev = GENESIS
    entries = [e for e in _read_chain() if not e.get("verdict_for")]
    for e in entries:
        if e.get("_corrupt"):
            return {"valid": False, "length": len(entries), "first_bad": None}
        expected = version_hash(e.get("v", 0), e.get("ts", ""), e.get("by", ""),
                                e.get("content_hash", ""),
                                e.get("check", {}).get("status", ""), e.get("prev_hash", ""))
        if e.get("prev_hash") != prev or e.get("hash") != expected:
            return {"valid": False, "length": len(entries), "first_bad": e.get("v")}
        prev = e["hash"]
    return {"valid": True, "length": len(entries), "first_bad": None}


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
