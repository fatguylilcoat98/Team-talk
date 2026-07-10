"""Private continuity journals — one per participant, owner-writes-only.

Each AI (plus Splendor and the Director) gets its own journal file:
memory/journals/{participant_id}_private.json. The write path is the
participant's OWN chat output (a JOURNAL: line) — there is no API that
lets anyone write another participant's journal, no automatic summaries,
and no AI ever writes for another AI. Chris can READ every journal (the
glass box has no hidden rooms), but he can't write them either.

Every entry is hash-chained: content_hash + previous_hash + timestamp +
writer. A single modified byte invalidates every entry after it. The
"signature" is writer+hash — honest tamper-EVIDENCE on a single-owner
box, not cryptographic identity, and we don't pretend otherwise.

The `recognized` field ("do I recognize this room/continuity as mine?")
is set ONLY by the participant, in its own marker: true, false, or
uncertain. Never inferred. Unstated stays unstated.
"""

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import List, Optional, Tuple

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
JOURNALS_DIR = os.path.join(BASE_DIR, "memory", "journals")

GENESIS = "0" * 64
MAX_NOTE_CHARS = 600
MAX_PER_MESSAGE = 2
CONTEXT_ENTRIES = 8
RECOGNIZED_VALUES = ("true", "false", "uncertain", "unstated")

# JOURNAL: <note>
# JOURNAL[recognized=true, confidence=0.8, intent=continuity]: <note>
_JOURNAL_LINE = re.compile(
    r"^[ \t]*JOURNAL(?:\[(?P<flags>[^\]]*)\])?:[ \t]*(?P<note>.+?)[ \t]*$",
    re.MULTILINE,
)
_FLAG = re.compile(r"(\w+)\s*=\s*([^,\]]+)")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _safe_id(participant_id: str) -> Optional[str]:
    pid = re.sub(r"[^A-Za-z0-9_-]", "", str(participant_id or ""))[:40]
    return pid or None


def _path(participant_id: str) -> str:
    return os.path.join(JOURNALS_DIR, f"{participant_id}_private.json")


def _load(participant_id: str) -> List[dict]:
    try:
        with open(_path(participant_id), "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def _save(participant_id: str, entries: List[dict]) -> None:
    os.makedirs(JOURNALS_DIR, mode=0o700, exist_ok=True)
    tmp = f"{_path(participant_id)}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)
    os.replace(tmp, _path(participant_id))


def entry_hash(entry: dict) -> str:
    core = "|".join([
        str(entry.get("ts", "")), str(entry.get("writer", "")),
        str(entry.get("session", "")), str(entry.get("intent", "")),
        str(entry.get("continuity_note", "")), str(entry.get("recognized", "")),
        str(entry.get("confidence", "")), str(entry.get("prev_hash", "")),
    ])
    return _sha(core)


def write(participant_id: str, writer_name: str, session_id: str,
          continuity_note: str, intent: str = "",
          recognized: str = "unstated", confidence: Optional[float] = None) -> Optional[dict]:
    """Append one entry to the participant's OWN journal, hash-chained.

    The only caller is the chat pipeline handing over that participant's
    own JOURNAL: lines — ownership is enforced by construction.
    """
    pid = _safe_id(participant_id)
    if not pid or not (continuity_note or "").strip():
        return None
    entries = _load(pid)
    prev_hash = entries[-1]["hash"] if entries else GENESIS
    entry = {
        "id": uuid.uuid4().hex[:12],
        "version": len(entries) + 1,
        "ts": _now(),
        "writer": str(writer_name)[:60],
        "session": str(session_id)[:64],
        "intent": str(intent or "")[:200],
        "continuity_note": continuity_note.strip()[:MAX_NOTE_CHARS],
        "recognized": recognized if recognized in RECOGNIZED_VALUES else "unstated",
        "confidence": confidence,
        "prev_hash": prev_hash,
    }
    entry["content_hash"] = _sha(entry["continuity_note"])
    entry["hash"] = entry_hash(entry)
    entry["signature"] = f"{entry['writer']}:{entry['hash'][:16]}"
    entries.append(entry)
    _save(pid, entries)
    return entry


def read(participant_id: str) -> List[dict]:
    pid = _safe_id(participant_id)
    return _load(pid) if pid else []


def verify(participant_id: str) -> dict:
    """Recompute the participant's chain. Raw truth, no summaries."""
    entries = read(participant_id)
    prev = GENESIS
    for e in entries:
        if e.get("prev_hash") != prev:
            return {"valid": False, "length": len(entries),
                    "first_bad_version": e.get("version"), "reason": "broken chain link"}
        if entry_hash(e) != e.get("hash"):
            return {"valid": False, "length": len(entries),
                    "first_bad_version": e.get("version"), "reason": "entry hash mismatch"}
        if _sha(str(e.get("continuity_note", ""))) != e.get("content_hash"):
            return {"valid": False, "length": len(entries),
                    "first_bad_version": e.get("version"), "reason": "content hash mismatch"}
        prev = e.get("hash")
    return {"valid": True, "length": len(entries), "first_bad_version": None, "reason": None}


def list_journals() -> List[str]:
    try:
        names = os.listdir(JOURNALS_DIR)
    except OSError:
        return []
    return sorted(n[:-len("_private.json")] for n in names if n.endswith("_private.json"))


def extract(text: str) -> Tuple[str, List[dict]]:
    """Pull JOURNAL: lines out of a participant's response.

    Returns (cleaned text, [{note, intent, recognized, confidence}]).
    recognized/confidence come only from the participant's own flags —
    absent means "unstated", never a guess.
    """
    found = []
    for m in _JOURNAL_LINE.finditer(text):
        note = (m.group("note") or "").strip()
        if not note:
            continue
        flags = {}
        for fm in _FLAG.finditer(m.group("flags") or ""):
            flags[fm.group(1).lower()] = fm.group(2).strip()
        recognized = flags.get("recognized", "unstated").lower()
        if recognized not in RECOGNIZED_VALUES:
            recognized = "unstated"
        confidence = None
        if "confidence" in flags:
            try:
                confidence = max(0.0, min(1.0, float(flags["confidence"])))
            except ValueError:
                confidence = None
        found.append({"note": note, "intent": flags.get("intent", "")[:200],
                      "recognized": recognized, "confidence": confidence})
    if not found:
        return text, []
    cleaned = _JOURNAL_LINE.sub("", text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, found[:MAX_PER_MESSAGE]


def boot_block(participant_id: str, name: str) -> str:
    """The participant's boot packet: authenticated records, never fake
    continuity. This block is PRIVATE — only this participant sees it."""
    pid = _safe_id(participant_id)
    if not pid:
        return ""
    entries = _load(pid)
    if not entries:
        return (f"=== YOUR PRIVATE JOURNAL ({name} only — authenticated records) ===\n"
                f"(Empty. Nothing has been written yet — by you or anyone. "
                f"You do not 'remember'; you have records, and right now there are none.)")
    chain = verify(pid)
    status = "✓ hash chain valid" if chain["valid"] else \
        f"⚠ CHAIN BROKEN at entry v{chain['first_bad_version']} ({chain['reason']}) — treat later entries as unverified"
    lines = [
        f"=== YOUR PRIVATE JOURNAL ({name} only — authenticated records) ===",
        f"({chain['length']} entries · {status} · latest hash {entries[-1]['hash'][:12]}…)",
        "(These are records you wrote, not memories you experienced. Say 'my records show', never 'I remember'.)",
    ]
    for e in entries[-CONTEXT_ENTRIES:]:
        rec = e.get("recognized", "unstated")
        conf = f", conf {e['confidence']}" if e.get("confidence") is not None else ""
        lines.append(f"- [v{e['version']}, {e['ts'][:16]}Z, recognized={rec}{conf}] {e['continuity_note']}")
    return "\n".join(lines)
