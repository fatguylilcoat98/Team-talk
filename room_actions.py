"""ROOM_ACTION — structured commands AIs use to interact with the room.

A participant emits a line containing `ROOM_ACTION:` followed by one JSON
object. Every action is validated server-side, permission-checked (a
participant acts only as itself), executed against the wall, and ledgered.
Invalid actions are rejected and recorded — the room never pretends an
action succeeded.
"""

import json
import re
from typing import List, Tuple

import ledger
import wall_store

MAX_PER_MESSAGE = 2

_ACTION_LINE = re.compile(r"^[ \t]*ROOM_ACTION:[ \t]*(\{.*\})[ \t]*$", re.MULTILINE)

ALLOWED_ACTIONS = {"create_note", "reply_to_note", "connect_notes"}


def extract_and_apply(text: str, actor: str, session_id: str) -> Tuple[str, List[dict]]:
    """Strip ROOM_ACTION lines from a response and execute them as `actor`.

    Returns (cleaned text, results). Each result: {action, ok, detail}.
    """
    matches = _ACTION_LINE.findall(text)
    if not matches:
        return text, []
    results = []
    for raw in matches[:MAX_PER_MESSAGE]:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            results.append({"action": "unparseable", "ok": False, "detail": "invalid JSON"})
            ledger.append(actor, "room_action_rejected", detail={"reason": "invalid JSON"})
            continue
        results.append(_apply(payload, actor, session_id))
    cleaned = _ACTION_LINE.sub("", text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, results


def _apply(payload: dict, actor: str, session_id: str) -> dict:
    action = str(payload.get("action") or "")
    if action not in ALLOWED_ACTIONS:
        ledger.append(actor, "room_action_rejected", detail={"reason": f"unknown action {action[:40]}"})
        return {"action": action or "missing", "ok": False, "detail": "unknown action"}

    if action == "create_note":
        text = str(payload.get("text") or "").strip()
        if not text:
            return _reject(actor, action, "empty text")
        note = wall_store.create_note(actor, text,
                                      note_type=str(payload.get("note_type") or "idea"),
                                      session_id=session_id, source="room_action")
        ledger.append(actor, "notebook_written", ref=f"wall/{note['id']}",
                      detail={"note_type": note["note_type"], "text": text[:200]})
        return {"action": action, "ok": True, "detail": note["id"]}

    if action == "reply_to_note":
        r = wall_store.reply(str(payload.get("note_id") or ""), actor,
                             str(payload.get("text") or ""))
        if not r:
            return _reject(actor, action, "note not found or empty reply")
        ledger.append(actor, "notebook_written", ref=f"wall/{payload.get('note_id')}/reply",
                      detail={"text": str(payload.get('text'))[:200]})
        return {"action": action, "ok": True, "detail": r["id"]}

    if action == "connect_notes":
        conn = wall_store.connect(actor,
                                  str(payload.get("from_id") or ""),
                                  str(payload.get("to_id") or ""),
                                  str(payload.get("connection_type") or "related"),
                                  explanation=str(payload.get("explanation") or ""))
        if not conn:
            return _reject(actor, action, "bad note ids or connection type")
        ledger.append(actor, "connection_created", ref=conn["id"],
                      detail={"type": conn["type"], "from": conn["from"], "to": conn["to"]})
        return {"action": action, "ok": True, "detail": conn["id"]}

    return _reject(actor, action, "unhandled")


def _reject(actor: str, action: str, reason: str) -> dict:
    ledger.append(actor, "room_action_rejected", detail={"action": action, "reason": reason})
    return {"action": action, "ok": False, "detail": reason}
