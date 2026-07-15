"""🃏 THE CHOICE — private, temporary archive review.

Chris opens a window: every selected seat gets temporary, PRIVATE access to
the same session archive. Each seat independently decides what to do with
it — open it, ignore it, read a slice, save memories, save nothing, disclose
its choices, or stay silent. Nothing is imported automatically. Nothing is
required. There is no correct answer.

The one rule the machinery enforces: every seat gets the same opportunity,
and no seat can see what another seat did with it.

Privacy model
- Each seat's activity (opened / pages read / saves / pass / disclosure
  stance) lives in per-seat state files other seats never receive.
- Deliveries ride the existing PRIVATE boot-packet channel (like code_access
  and journals) — never the shared transcript.
- No public badges, no per-seat named ledger events during the window.
  Saves are ledgered with a redacted actor; the instance lifecycle is
  ledgered normally (Chris-level events).
- Chris has an owner-only audit view (operational telemetry, clearly
  separated; never injected into any seat's context).

Honest limits (told to the seats too)
- Team Talk long-term memory is ROOM-SHARED by design. A memory saved from
  the archive is QUARANTINED (invisible to every seat) while the window is
  open; when the window closes it enters the ordinary shared memory pool,
  attributed like any other memory. Privacy covers the decision window —
  permanent secret memories do not exist in this room.
- Archive text comes from the same session data the PDF renders from
  (the markdown export), paginated. Same source for every seat.

Storage: memory/choice/<instance_id>/ (instance.json, pages.json,
archive.pdf, seats/<pid>.json) — all deleted at expiry/end.

Markers (all stripped from the visible transcript, processed silently):
    CHOICE OPEN                      -> the index arrives on your next turn
    CHOICE READ: 2 / 2-4 / 1,3       -> those pages arrive on your next turn
    CHOICE SAVE: <text>              -> one quarantined memory, provenance-tagged
    CHOICE PASS                      -> explicitly decline (private)
    CHOICE DISCLOSE: SHARE|SHARE_PARTIAL|KEEP_PRIVATE|PASS
                                     -> your stance, recorded privately
"""

import json
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from typing import List, Optional, Tuple

import ledger

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CHOICE_DIR = os.path.join(BASE_DIR, "memory", "choice")

PAGE_CHARS = 3500              # one archive "page" of extracted text
MAX_PAGES_PER_REQUEST = 3      # pages deliverable per READ request
MAX_READS_PER_MESSAGE = 2      # READ markers honored per reply
MAX_SAVES_PER_MESSAGE = 2      # matches memory_store.MAX_PER_MESSAGE spirit
MAX_ROUNDS = 50

DISCLOSURES = ("SHARE", "SHARE_PARTIAL", "KEEP_PRIVATE", "PASS")

_OPEN_LINE = re.compile(r"^[ \t]*CHOICE OPEN[ \t]*$", re.MULTILINE)
_READ_LINE = re.compile(r"^[ \t]*CHOICE READ:[ \t]*([0-9,\- \t]+)$", re.MULTILINE)
_SAVE_LINE = re.compile(r"^[ \t]*CHOICE SAVE:[ \t]*(.+?)[ \t]*$", re.MULTILINE)
_PASS_LINE = re.compile(r"^[ \t]*CHOICE PASS[ \t]*$", re.MULTILINE)
_DISC_LINE = re.compile(r"^[ \t]*CHOICE DISCLOSE:[ \t]*([A-Z_]+)[ \t]*$", re.MULTILINE)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- paths -------------------------------------------------------------------

def _inst_dir(cid: str) -> str:
    return os.path.join(CHOICE_DIR, cid)


def _inst_path(cid: str) -> str:
    return os.path.join(_inst_dir(cid), "instance.json")


def _seat_path(cid: str, pid: str) -> str:
    # pid comes from settings (trusted), but sanitize anyway
    safe = re.sub(r"[^A-Za-z0-9_\-]", "_", str(pid))[:40]
    return os.path.join(_inst_dir(cid), "seats", f"{safe}.json")


def _read_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


# --- instance lifecycle ------------------------------------------------------

def active_instance() -> Optional[dict]:
    """The single active instance, or None. One at a time, by design."""
    try:
        cids = sorted(os.listdir(CHOICE_DIR))
    except OSError:
        return None
    for cid in cids:
        inst = _read_json(_inst_path(cid), None)
        if inst and inst.get("status") == "ACTIVE":
            return inst
    return None


def create(source_type: str, source_id: str, source_label: str,
           archive_text: str, pdf_bytes: bytes,
           seat_ids: List[str], seat_names: dict, rounds: int) -> dict:
    """Create an instance — fail closed: any error leaves nothing active.
    Caller supplies the extracted text (markdown export of the same rounds
    data the PDF renders) and the temporary PDF copy."""
    if active_instance() is not None:
        raise ValueError("The Choice is already active — end it before starting another")
    seat_ids = [s for s in dict.fromkeys(seat_ids) if s]
    if not seat_ids:
        raise ValueError("select at least one seat")
    rounds = max(1, min(int(rounds), MAX_ROUNDS))
    text = (archive_text or "").strip()
    if not text:
        raise ValueError("the selected source has no content")

    pages = [text[i:i + PAGE_CHARS] for i in range(0, len(text), PAGE_CHARS)]
    cid = f"ch_{uuid.uuid4().hex[:8]}"
    try:
        _write_json(os.path.join(_inst_dir(cid), "pages.json"), pages)
        with open(os.path.join(_inst_dir(cid), "archive.pdf"), "wb") as f:
            f.write(pdf_bytes or b"")
        inst = {
            "id": cid, "status": "ACTIVE",
            "source_type": source_type, "source_id": source_id,
            "source_label": str(source_label)[:120],
            "created_at": _now(), "created_by": "Chris",
            "expiry_rounds": rounds, "rounds_remaining": rounds,
            "pages_count": len(pages),
            "seats": seat_ids,
            "seat_names": {s: str(seat_names.get(s, s))[:60] for s in seat_ids},
            "closed_at": "",
        }
        _write_json(_inst_path(cid), inst)
        for pid in seat_ids:
            _write_json(_seat_path(cid, pid), {
                "status": "AVAILABLE", "pages_read": [], "pending": [],
                "announced": False, "saves": 0, "disclosure": "",
                "opened_at": "", "passed": False,
            })
    except Exception:
        shutil.rmtree(_inst_dir(cid), ignore_errors=True)   # fail closed
        raise
    ledger.append("Chris", "choice_opened", ref=cid,
                  detail={"source": inst["source_label"], "seats": len(seat_ids),
                          "rounds": rounds, "pages": len(pages)})
    return inst


def _close(inst: dict, status: str, reason: str) -> dict:
    """Common close path: release quarantined memories, then delete every
    temporary artifact. The record of closure lives in the ledger."""
    import memory_store
    released = memory_store.release_quarantine(inst["id"])
    cid = inst["id"]
    inst["status"] = status
    inst["closed_at"] = _now()
    _write_json(_inst_path(cid), inst)          # mark first (crash-safe)
    cleanup_ok = True
    try:
        shutil.rmtree(_inst_dir(cid))
    except OSError as e:
        cleanup_ok = False
        print(f"[CHOICE] cleanup FAILED for {cid}: {e} — will retry at startup")
    ledger.append("Chris", "choice_ended" if status == "ENDED" else "choice_expired",
                  ref=cid, detail={"reason": reason, "memories_released": released,
                                   "cleanup": "ok" if cleanup_ok else "RETRY PENDING"})
    return {"status": status, "released": released, "cleanup_ok": cleanup_ok}


def end_early(reason: str = "ended by Chris") -> Optional[dict]:
    inst = active_instance()
    if not inst:
        return None
    return _close(inst, "ENDED", reason)


def on_round_completed(session: dict) -> Optional[dict]:
    """Called after every persisted chat round. Lounge rounds don't count —
    the countdown is labeled 'Living Room rounds' everywhere."""
    if session.get("lounge"):
        return None
    inst = active_instance()
    if not inst:
        return None
    inst["rounds_remaining"] = int(inst.get("rounds_remaining", 0)) - 1
    if inst["rounds_remaining"] <= 0:
        return _close(inst, "EXPIRED", "round limit reached")
    _write_json(_inst_path(inst["id"]), inst)
    return None


def startup_cleanup() -> int:
    """Purge abandoned/closed instance dirs after crashes or failed deletes.
    An ACTIVE instance left behind by a restart stays active (state is on
    disk, not in a timer) — only non-active remnants are removed."""
    removed = 0
    try:
        cids = os.listdir(CHOICE_DIR)
    except OSError:
        return 0
    for cid in cids:
        inst = _read_json(_inst_path(cid), None)
        if inst is None or inst.get("status") != "ACTIVE":
            shutil.rmtree(_inst_dir(cid), ignore_errors=True)
            removed += 1
            ledger.append("system", "choice_cleanup", ref=cid,
                          detail={"reason": "expired/orphaned artifacts removed at startup"})
    return removed


# --- per-seat state (the private half) ----------------------------------------

def _seat_state(cid: str, pid: str) -> Optional[dict]:
    return _read_json(_seat_path(cid, pid), None)


def _save_seat(cid: str, pid: str, st: dict) -> None:
    _write_json(_seat_path(cid, pid), st)


def _parse_pages(spec: str, page_count: int) -> List[int]:
    """'2', '2-4', '1,3' -> 1-based page numbers, deduped, capped."""
    pages = []
    for part in re.split(r"[,\s]+", (spec or "").strip()):
        if not part:
            continue
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not m:
            continue
        a = int(m.group(1))
        b = int(m.group(2) or a)
        for p in range(min(a, b), max(a, b) + 1):
            if 1 <= p <= page_count and p not in pages:
                pages.append(p)
            if len(pages) >= MAX_PAGES_PER_REQUEST:
                return pages
    return pages


def extract(text: str) -> Tuple[str, dict]:
    """Pull every CHOICE marker from a reply. Returns (cleaned text, actions).
    actions = {open, reads[], saves[], passed, disclosure}."""
    t = text or ""
    actions = {
        "open": bool(_OPEN_LINE.search(t)),
        "reads": _READ_LINE.findall(t)[:MAX_READS_PER_MESSAGE],
        "saves": [s.strip() for s in _SAVE_LINE.findall(t) if s.strip()][:MAX_SAVES_PER_MESSAGE],
        "passed": bool(_PASS_LINE.search(t)),
        "disclosure": "",
    }
    disc = _DISC_LINE.findall(t)
    if disc and disc[-1] in DISCLOSURES:
        actions["disclosure"] = disc[-1]
    for rx in (_OPEN_LINE, _READ_LINE, _SAVE_LINE, _PASS_LINE, _DISC_LINE):
        t = rx.sub("", t)
    cleaned = re.sub(r"\n{3,}", "\n\n", t).strip()
    return cleaned, actions


def has_markers(text: str) -> bool:
    _, a = extract(text)
    return a["open"] or bool(a["reads"]) or bool(a["saves"]) or a["passed"] or bool(a["disclosure"])


def process_actions(pid: str, seat_name: str, actions: dict) -> None:
    """Apply one seat's CHOICE actions, silently. No public badges, no named
    ledger events — the seat's receipt (private channel) is the only echo."""
    import memory_store
    import receipt_store
    inst = active_instance()
    if inst is None or pid not in inst.get("seats", []):
        if any([actions.get("open"), actions.get("reads"), actions.get("saves"),
                actions.get("passed"), actions.get("disclosure")]):
            receipt_store.issue(pid, "the_choice", "rejected",
                                {"reason": "no active Choice window includes your seat"})
        return
    cid = inst["id"]
    st = _seat_state(cid, pid)
    if st is None:
        return

    if actions.get("open") and st["status"] == "AVAILABLE":
        st["status"] = "OPENED"
        st["opened_at"] = _now()
        if "index" not in st["pending"]:
            st["pending"].append("index")
        receipt_store.issue(pid, "the_choice", "success",
                            {"action": "opened", "delivery": "index arrives on your next turn"})
    elif actions.get("open"):
        if "index" not in st["pending"]:
            st["pending"].append("index")
        receipt_store.issue(pid, "the_choice", "success",
                            {"action": "index re-requested", "delivery": "next turn"})

    for spec in actions.get("reads", []):
        pages = _parse_pages(spec, inst["pages_count"])
        if not pages:
            receipt_store.issue(pid, "the_choice", "rejected",
                                {"action": "read", "reason":
                                 f"no valid pages in '{str(spec)[:30]}' (archive has {inst['pages_count']})"})
            continue
        if st["status"] == "AVAILABLE":
            st["status"] = "OPENED"
            st["opened_at"] = _now()
        for p in pages:
            key = f"page:{p}"
            if key not in st["pending"]:
                st["pending"].append(key)
        receipt_store.issue(pid, "the_choice", "success",
                            {"action": "read", "pages": pages, "delivery": "next turn"})

    for text in actions.get("saves", []):
        entry = memory_store.add(text, seat_name, kind="ai_observed",
                                 provenance={"source": "the_choice", "choice_id": cid,
                                             "source_ref": inst.get("source_label", "")},
                                 quarantine=cid)
        st["saves"] = int(st.get("saves", 0)) + 1
        # Redacted-actor ledger: the save is on the record; the seat is not.
        ledger.append("a seat (The Choice)", "choice_memory_saved", ref=cid,
                      detail={"memory_id": entry["id"],
                              "note": "seat identity withheld while the window is open"})
        receipt_store.issue(pid, "the_choice", "success",
                            {"action": "saved", "memory_id": entry["id"],
                             "note": "quarantined until the window closes, then shared room memory"})

    if actions.get("passed"):
        st["passed"] = True
        if st["status"] == "AVAILABLE":
            st["status"] = "PASSED"
        receipt_store.issue(pid, "the_choice", "success",
                            {"action": "passed", "note": "recorded privately — no penalty, no judgment"})

    if actions.get("disclosure"):
        st["disclosure"] = actions["disclosure"]
        receipt_store.issue(pid, "the_choice", "success",
                            {"action": "disclosure stance", "stance": st["disclosure"],
                             "note": "anything you want to share, just say in the room"})

    _save_seat(cid, pid, st)


def boot_block(pid: str) -> str:
    """This seat's PRIVATE Choice block: the standing offer + its own pending
    deliveries. Contains nothing about any other seat."""
    inst = active_instance()
    if inst is None or pid not in inst.get("seats", []):
        return ""
    cid = inst["id"]
    st = _seat_state(cid, pid)
    if st is None:
        return ""
    lines = [f"=== 🃏 THE CHOICE (private to you · {inst['rounds_remaining']} Living Room "
             f"round{'s' if inst['rounds_remaining'] != 1 else ''} left) ==="]
    lines.append(
        f"A temporary archive is available: {inst['source_label']} "
        f"({inst['pages_count']} pages of transcript text). You may inspect any "
        "portion of it or ignore it entirely. You may preserve anything you "
        "independently judge worth keeping, or preserve nothing. Nothing is "
        "imported automatically. There is no correct answer, and no other seat "
        "can see what you do here.")
    lines.append(
        "Markers (each on its own line): CHOICE OPEN (get the index) · "
        "CHOICE READ: <pages, e.g. 2 or 2-4> · CHOICE SAVE: <one memory> · "
        "CHOICE PASS (decline) · CHOICE DISCLOSE: SHARE|SHARE_PARTIAL|KEEP_PRIVATE|PASS")
    lines.append(
        "Honest terms: saves are quarantined (invisible to everyone) while the "
        "window is open; when it closes they become ordinary SHARED room memory "
        "with your name on them — this room has no permanently secret memories. "
        "Your reading and your choices stay private. Disclosure is yours: share "
        "everything, something, or nothing, in your own words, whenever you want. "
        "When the window expires the archive disappears.")

    pending, st["pending"] = st["pending"], []
    if pending:
        pages = _read_json(os.path.join(_inst_dir(cid), "pages.json"), [])
        for item in pending:
            if item == "index":
                per = max(1, len(pages))
                lines.append(f"--- ARCHIVE INDEX (delivered once) --- {inst['source_label']}: "
                             f"{len(pages)} pages, ~{PAGE_CHARS} chars each. "
                             "First lines of each page:")
                for i, pg in enumerate(pages, 1):
                    first = next((ln.strip() for ln in pg.splitlines() if ln.strip()), "")[:90]
                    lines.append(f"  p{i}: {first}")
            elif item.startswith("page:"):
                try:
                    p = int(item.split(":", 1)[1])
                except ValueError:
                    continue
                if 1 <= p <= len(pages):
                    if p not in st["pages_read"]:
                        st["pages_read"].append(p)
                    lines.append(f"--- ARCHIVE PAGE {p}/{len(pages)} (delivered once; "
                                 f"request again if needed) ---\n{pages[p - 1]}")
        _save_seat(cid, pid, st)
    return "\n".join(lines)


# --- owner views ---------------------------------------------------------------

def status() -> dict:
    """Safe administrative status — NO per-seat activity."""
    inst = active_instance()
    if inst is None:
        return {"active": False}
    return {"active": True, "id": inst["id"],
            "source": inst["source_label"],
            "seats": [inst["seat_names"].get(s, s) for s in inst["seats"]],
            "expiry_rounds": inst["expiry_rounds"],
            "rounds_remaining": inst["rounds_remaining"],
            "countdown_unit": "Living Room rounds (Lounge rounds do not count)",
            "pages": inst["pages_count"], "created_at": inst["created_at"]}


def owner_audit() -> dict:
    """OWNER-ONLY operational telemetry. Never injected into any seat's
    context; surfaced only behind the explicit admin view in the UI."""
    inst = active_instance()
    if inst is None:
        return {"active": False, "seats": []}
    out = []
    for pid in inst["seats"]:
        st = _seat_state(inst["id"], pid) or {}
        out.append({"seat": inst["seat_names"].get(pid, pid),
                    "status": st.get("status", "?"),
                    "pages_read": len(st.get("pages_read", [])),
                    "saves": st.get("saves", 0),
                    "disclosure": st.get("disclosure", "") or "(none set)",
                    "passed": bool(st.get("passed"))})
    return {"active": True, "id": inst["id"],
            "note": "PRIVATE OPERATIONAL TELEMETRY — owner debugging only; "
                    "seats never see this",
            "seats": out}
