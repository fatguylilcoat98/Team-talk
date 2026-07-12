"""🧱 The Glass — one-way glass over the room's answers.

When the glass is UP, each seat still knows the others are here and still
sees that they answered — but the ANSWER TEXT is hidden. They respond blind
to each other. Chris (the observer) always sees everything; the glass only
sits between the seats.

One toggle:
- raise it  -> new answers get sealed (hidden from the other seats)
- lower it  -> stop sealing AND unseal what was sealed (the windows open)

The current up/down state lives here (memory/glass.json). The per-answer
`sealed` flag lives on each response inside the session record — so a
revealed answer stays revealed even after the toggle moves again.
"""

import json
import os
from threading import Lock

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(BASE_DIR, "memory")
STATE_PATH = os.path.join(STATE_DIR, "glass.json")

_lock = Lock()


def is_up() -> bool:
    """True when the glass is raised (answers get sealed)."""
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return bool(json.load(f).get("up"))
    except (OSError, json.JSONDecodeError):
        return False


def set_up(up: bool) -> bool:
    with _lock:
        os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
        tmp = f"{STATE_PATH}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"up": bool(up)}, f)
        os.replace(tmp, STATE_PATH)
    return bool(up)


def unseal_session(session: dict) -> int:
    """Drop the glass on one session: clear every response's `sealed` flag.
    Returns how many answers were revealed. Mutates `session` in place;
    the caller saves it."""
    revealed = 0
    for rnd in session.get("rounds", []):
        for resp in rnd.get("responses", []):
            if resp.pop("sealed", None):
                revealed += 1
    return revealed
