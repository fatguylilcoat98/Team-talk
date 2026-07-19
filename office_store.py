"""Offices — institutional seats that outlive whoever currently occupies them.

An OFFICE is a standing role in the room (the Pattern Catcher's ledger desk, say).
A PARTICIPANT is a model configured in settings. The two are stored separately on
purpose: DeepSeek currently sits in the Pattern Catcher's office, but if Gemini takes
the seat next month the office, its tools, and its whole assignment history survive
untouched. Nothing about the office lives in the participant record.

Append-only, in the spirit of ledger.py and reasoning_store.py: assignments are never
edited or deleted, only superseded by a later assignment. `occupant()` is therefore a
DERIVED fact — the last assignment wins — and the full history stays readable, so
"who held this office in round 12 of session X" remains answerable forever.

Storage: memory/offices.jsonl, plus a Glass Box ledger event per assignment so office
changes inherit the room's tamper-evident hash chain.
"""

import json
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional

import ledger

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STORE_DIR = os.path.join(BASE_DIR, "memory")
OFFICES_PATH = os.path.join(STORE_DIR, "offices.jsonl")

SCHEMA_VERSION = 1

# ---- the offices themselves --------------------------------------------------

PATTERN_CATCHER = "pattern_catcher"

OFFICES: Dict[str, dict] = {
    PATTERN_CATCHER: {
        "id": PATTERN_CATCHER,
        "title": "Pattern Catcher",
        "purpose": ("Holds the room's institutional memory. Searches the reasoning "
                    "ledger for prior positions, corrections, retractions, and drift "
                    "in apparent consensus — from records, not recollection."),
        # Capabilities are granted to the OFFICE. Whoever occupies it gets them.
        "capabilities": ["ledger_query"],
    },
}

#: Sentinel for "the office is currently empty".
VACANT = None


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _append(obj: dict) -> None:
    os.makedirs(STORE_DIR, mode=0o700, exist_ok=True)
    with open(OFFICES_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")


def _read() -> List[dict]:
    out = []
    try:
        with open(OFFICES_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return out


def assign(office_id: str, participant_id: Optional[str], by: str = "chris") -> dict:
    """Seat a participant in an office (or vacate it with participant_id=None).

    Never mutates a previous assignment — the record is the history."""
    if office_id not in OFFICES:
        raise ValueError(f"unknown office: {office_id}")
    rec = {
        "schema": SCHEMA_VERSION,
        "office": office_id,
        "participant_id": participant_id,
        "assigned_by": by,
        "ts": _now(),
    }
    _append(rec)
    ledger.append(by, "office_assigned" if participant_id else "office_vacated",
                  ref=office_id, detail={"participant_id": participant_id})
    return rec


def occupant(office_id: str) -> Optional[str]:
    """Who holds this office right now — DERIVED from the last assignment."""
    holder = VACANT
    for rec in _read():
        if rec.get("office") == office_id:
            holder = rec.get("participant_id")
    return holder


def history(office_id: str) -> List[dict]:
    """Every assignment this office has ever had, oldest first."""
    return [r for r in _read() if r.get("office") == office_id]


def holds(office_id: str, participant_id: str) -> bool:
    """Does THIS participant currently hold the office? The single gate every
    office-granted capability should ask before doing anything."""
    return bool(participant_id) and occupant(office_id) == participant_id


def capabilities_for(participant_id: str) -> List[str]:
    """Every capability this participant has by virtue of the offices they hold.
    A participant with no office gets an empty list — capabilities are never
    attached to a model, only to a seat."""
    caps: List[str] = []
    for oid, office in OFFICES.items():
        if holds(oid, participant_id):
            caps.extend(office.get("capabilities", []))
    return caps


def describe(office_id: str) -> dict:
    """Office + who sits in it, for the UI and for audit."""
    office = dict(OFFICES.get(office_id) or {})
    office["occupant"] = occupant(office_id)
    office["assignments"] = len(history(office_id))
    return office


def all_offices() -> List[dict]:
    return [describe(oid) for oid in OFFICES]
