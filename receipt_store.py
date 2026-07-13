"""System Action Receipts — proof that an action actually executed.

Every server-executed action a participant performs (wall actions, mail,
journal writes, memory saves, questions, About Me) produces a receipt.
On the participant's NEXT turn its boot context includes those receipts —
and only a receipt justifies saying "I did X". Rejected actions get
receipts too, so a participant knows its action FAILED instead of
narrating success.

This closes the gap between talking about doing something and actually
doing it. Storage: memory/receipts.json.
"""

import json
import os
import uuid
from datetime import datetime, timezone
from typing import List, Optional

import ledger

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RECEIPTS_DIR = os.path.join(BASE_DIR, "memory")
RECEIPTS_PATH = os.path.join(RECEIPTS_DIR, "receipts.json")

MAX_PER_PARTICIPANT = 30
BOOT_LIMIT = 8


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load() -> List[dict]:
    try:
        with open(RECEIPTS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def _save(receipts: List[dict]) -> None:
    os.makedirs(RECEIPTS_DIR, mode=0o700, exist_ok=True)
    # cap per participant, keep newest
    by_pid = {}
    for r in receipts:
        by_pid.setdefault(r.get("participant_id"), []).append(r)
    kept = []
    for pid, items in by_pid.items():
        # A receipt aging out at the cap before it was ever delivered would
        # erase a seat's proof that an action happened — the exact "did it
        # happen?" gap this store exists to close. Record it before it goes.
        if len(items) > MAX_PER_PARTICIPANT:
            for dropped in items[:-MAX_PER_PARTICIPANT]:
                if not dropped.get("delivered"):
                    ledger.append(
                        pid or "system", "receipt_evicted_undelivered",
                        ref=dropped.get("id") or "",
                        detail={"action": dropped.get("action"),
                                "status": dropped.get("status"),
                                "reason": f"aged out at the {MAX_PER_PARTICIPANT}-receipt cap before delivery"})
        kept.extend(items[-MAX_PER_PARTICIPANT:])
    kept.sort(key=lambda r: r.get("ts", ""))
    tmp = f"{RECEIPTS_PATH}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(kept, f, indent=2, ensure_ascii=False)
    os.replace(tmp, RECEIPTS_PATH)


def issue(participant_id: str, action: str, status: str,
          detail: Optional[dict] = None, ledger_ref: str = "") -> dict:
    receipt = {
        "id": f"rcp_{uuid.uuid4().hex[:10]}",
        "ts": _now(),
        "participant_id": str(participant_id)[:40],
        "action": str(action)[:60],
        "status": status if status in ("success", "rejected") else "success",
        "detail": detail or {},
        "ledger_ref": str(ledger_ref)[:120],
        "delivered": False,
    }
    receipts = _load()
    receipts.append(receipt)
    _save(receipts)
    return receipt


def list_receipts(participant_id: Optional[str] = None, limit: int = 50) -> List[dict]:
    receipts = _load()
    if participant_id:
        receipts = [r for r in receipts if r.get("participant_id") == participant_id]
    return receipts[-max(1, min(limit, 500)):]


def boot_block(participant_id: str) -> str:
    """Undelivered receipts for this participant's next turn. Marks delivery."""
    receipts = _load()
    fresh = [r for r in receipts
             if r.get("participant_id") == participant_id and not r.get("delivered")]
    if not fresh:
        return ""
    shown = fresh[-BOOT_LIMIT:]
    for r in shown:
        r["delivered"] = True
    _save(receipts)
    lines = ["=== SYSTEM ACTION RECEIPTS (server-executed results of YOUR past actions) ===",
             "(Only a receipt justifies claiming you did something. A rejected receipt "
             "means it did NOT happen — say so plainly.)"]
    for r in shown:
        mark = "✓" if r["status"] == "success" else "✗ REJECTED"
        extras = ", ".join(f"{k}={v}" for k, v in list(r.get("detail", {}).items())[:4])
        lines.append(f"- [{r['id']}] {mark} {r['action']} @ {r['ts'][:16]}Z"
                     + (f" ({extras})" if extras else ""))
    return "\n".join(lines)
