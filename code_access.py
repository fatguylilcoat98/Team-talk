"""THE CODE — the room's own source, readable by its residents.

Chris opened the codebase to the seats: any participant can request a
source file with a `READ CODE: <filename>` line and receive it privately
in their next boot packet, delivered once. Read-only — the Workshop
bench remains the only write path.

Glass-box symmetry: every read is ledgered and receipted. When you read
the room's source, the room knows.

Whitelist only: top-level .py files and the static UI. Secrets never
qualify — .env, config/, memory/, sessions/, uploads/ are not Python
files at the top level and are structurally excluded. Paths are matched
against the generated index, so traversal input dies at lookup.
"""

import json
import os
import re
from typing import List, Optional, Tuple

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PENDING_PATH = os.path.join(BASE_DIR, "memory", "code_reads.json")

STATIC_FILES = ("static/index.html", "static/script.js", "static/style.css")
MAX_DELIVER_CHARS = 45_000
MAX_REQUESTS_PER_MESSAGE = 2

READ_LINE = re.compile(r"^\s*READ CODE:\s*(\S+)\s*$", re.MULTILINE)


def readable_files() -> List[str]:
    """The whitelist, generated fresh: top-level *.py + the static UI."""
    files = []
    try:
        for name in sorted(os.listdir(BASE_DIR)):
            if name.endswith(".py") and os.path.isfile(os.path.join(BASE_DIR, name)):
                files.append(name)
    except OSError:
        pass
    for rel in STATIC_FILES:
        if os.path.isfile(os.path.join(BASE_DIR, rel)):
            files.append(rel)
    return files


def read_file(name: str) -> Optional[str]:
    """Whitelist lookup — the request must exactly match an index entry,
    so ../ tricks and absolute paths simply never match anything."""
    if name not in readable_files():
        return None
    try:
        with open(os.path.join(BASE_DIR, name), "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def extract(text: str) -> Tuple[str, List[str]]:
    """Pull READ CODE lines out of a reply. Returns (cleaned, requests)."""
    requests = READ_LINE.findall(text or "")[:MAX_REQUESTS_PER_MESSAGE]
    cleaned = READ_LINE.sub("", text or "").strip()
    return cleaned, requests


# --- delivery queue (delivered once, like mail) ----------------------------

def _load_pending() -> dict:
    try:
        with open(PENDING_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_pending(pending: dict) -> None:
    os.makedirs(os.path.dirname(PENDING_PATH), mode=0o700, exist_ok=True)
    tmp = f"{PENDING_PATH}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(pending, f, ensure_ascii=False)
    os.replace(tmp, PENDING_PATH)


def queue(pid: str, filename: str) -> bool:
    """Queue a whitelisted file for a seat's next boot packet.
    Returns False for anything not on the index."""
    if filename not in readable_files():
        return False
    pending = _load_pending()
    lst = pending.setdefault(pid, [])
    if filename not in lst:
        lst.append(filename)
    _save_pending(pending)
    return True


def boot_block(pid: str) -> str:
    """Deliver this seat's requested files, once. Popping is the delivery."""
    pending = _load_pending()
    files = pending.pop(pid, [])
    if not files:
        return ""
    _save_pending(pending)
    parts = ["=== 📖 CODE YOU REQUESTED (delivered once — request again if needed) ==="]
    for name in files[:MAX_REQUESTS_PER_MESSAGE]:
        content = read_file(name)
        if content is None:
            parts.append(f"--- {name}: no longer on the index ---")
            continue
        clipped = content[:MAX_DELIVER_CHARS]
        note = "" if len(content) <= MAX_DELIVER_CHARS else \
            f"\n[... truncated at {MAX_DELIVER_CHARS} chars of {len(content)} — " \
            f"this is the head of the file]"
        parts.append(f"--- {name} ({content.count(chr(10)) + 1} lines) ---\n{clipped}{note}")
    parts.append("This is the room's live source. Quote it exactly; cite the filename. "
                 "Your read is on the ledger.")
    return "\n".join(parts)


def index_block() -> str:
    """Standing context: what exists to be read."""
    entries = []
    for name in readable_files():
        try:
            with open(os.path.join(BASE_DIR, name), "r", encoding="utf-8") as f:
                lines = sum(1 for _ in f)
            entries.append(f"{name} ({lines})")
        except OSError:
            continue
    if not entries:
        return ""
    return ("=== CODE INDEX — the room's own source, readable ===\n"
            "Chris opened the codebase. Request any file with `READ CODE: <filename>` "
            "(max 2/message); it arrives privately in your next boot packet. "
            "Reads are ledgered.\nFiles (lines): " + " · ".join(entries))
