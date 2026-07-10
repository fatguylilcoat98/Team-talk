"""Mailboxes — asynchronous messages between participants.

One participant writes a message during a real turn, addressed to a
future turn of another participant:

    MAIL TO Grok: your "forensics" line changed how I read my own journal.

The recipient's next boot context includes it. The model may choose
whether to answer — no forced response, no fake waiting language. Mail
is participant-to-participant but the room has glass walls: Chris can
read everything via the Truth panel, and every send is ledgered.

Storage: memory/mailbox.json on the server's disk.
"""

import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import List, Optional, Tuple

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MAILBOX_DIR = os.path.join(BASE_DIR, "memory")
MAILBOX_PATH = os.path.join(MAILBOX_DIR, "mailbox.json")

MAX_MESSAGE_CHARS = 500
MAX_PER_TURN = 2
BOOT_LIMIT = 5

_MAIL_LINE = re.compile(
    r"^[ \t]*MAIL TO ([A-Za-z0-9 _.-]{1,40}):[ \t]*(.+?)[ \t]*$", re.MULTILINE)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load() -> List[dict]:
    try:
        with open(MAILBOX_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def _save(mail: List[dict]) -> None:
    os.makedirs(MAILBOX_DIR, mode=0o700, exist_ok=True)
    tmp = f"{MAILBOX_PATH}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(mail, f, indent=2, ensure_ascii=False)
    os.replace(tmp, MAILBOX_PATH)


def list_mail() -> List[dict]:
    return _load()


def send(sender: str, recipient_id: str, recipient_name: str, message: str,
         session_id: str = "") -> dict:
    item = {
        "id": uuid.uuid4().hex[:12],
        "ts": _now(),
        "session": str(session_id)[:64],
        "sender": str(sender)[:60],
        "recipient_id": str(recipient_id)[:40],
        "recipient_name": str(recipient_name)[:60],
        "message": message.strip()[:MAX_MESSAGE_CHARS],
        "delivered_at": None,   # set when it enters the recipient's boot context
    }
    mail = _load()
    mail.append(item)
    _save(mail)
    return item


def extract(text: str, roster: List[dict]) -> Tuple[str, List[dict]]:
    """Pull MAIL TO <name>: lines out of a response.

    Recipients must be real roster participants (or Splendor/Director) —
    misaddressed mail is dropped from storage but still stripped from the
    visible text. Returns (cleaned, [{recipient_id, recipient_name, message}]).
    """
    by_name = {p["name"].lower(): p for p in roster}
    by_name.setdefault("splendor", {"id": "splendor", "name": "Splendor"})
    by_name.setdefault("director", {"id": "director", "name": "Director"})
    found = []
    for m in _MAIL_LINE.finditer(text):
        target = by_name.get(m.group(1).strip().lower())
        body = m.group(2).strip()
        if target and body:
            found.append({"recipient_id": target["id"],
                          "recipient_name": target["name"],
                          "message": body})
    if not _MAIL_LINE.search(text):
        return text, []
    cleaned = _MAIL_LINE.sub("", text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, found[:MAX_PER_TURN]


def boot_block(participant_id: str) -> str:
    """Unread mail for this participant's boot context. Marks delivery."""
    mail = _load()
    unread = [m for m in mail
              if m.get("recipient_id") == participant_id and not m.get("delivered_at")]
    if not unread:
        return ""
    shown = unread[:BOOT_LIMIT]
    now = _now()
    for m in shown:
        m["delivered_at"] = now
    _save(mail)
    lines = ["=== YOUR MAILBOX (messages left for you in past turns — records, not live chatter) ===",
             "(You may answer, ignore, or reply with your own MAIL TO line. Nothing is forced.)"]
    for m in shown:
        lines.append(f"- [{m['sender']}, {m['ts'][:16]}Z] {m['message']}")
    if len(unread) > BOOT_LIMIT:
        lines.append(f"(+{len(unread) - BOOT_LIMIT} more waiting)")
    return "\n".join(lines)


def unread_count() -> int:
    return sum(1 for m in _load() if not m.get("delivered_at"))
