"""About Me — self-authored, append-only, one page per participant.

Written only via the participant's own ABOUT ME: line. Nobody edits it —
not Chris, not another AI. History stays visible (append-only, versioned).

Storage: memory/about.json.
"""

import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Tuple

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ABOUT_DIR = os.path.join(BASE_DIR, "memory")
ABOUT_PATH = os.path.join(ABOUT_DIR, "about.json")

MAX_LINE = 300
MAX_PER_MESSAGE = 2

_ABOUT_LINE = re.compile(r"^[ \t]*ABOUT ME:[ \t]*(.+?)[ \t]*$", re.MULTILINE)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load() -> Dict[str, List[dict]]:
    try:
        with open(ABOUT_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(data: Dict[str, List[dict]]) -> None:
    os.makedirs(ABOUT_DIR, mode=0o700, exist_ok=True)
    tmp = f"{ABOUT_PATH}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, ABOUT_PATH)


def append(participant_id: str, text: str) -> dict:
    pid = re.sub(r"[^A-Za-z0-9_-]", "", str(participant_id or ""))[:40]
    data = _load()
    entries = data.setdefault(pid, [])
    entry = {"id": uuid.uuid4().hex[:8], "ts": _now(),
             "text": text.strip()[:MAX_LINE], "version": len(entries) + 1}
    entries.append(entry)
    _save(data)
    return entry


def read(participant_id: str) -> List[dict]:
    pid = re.sub(r"[^A-Za-z0-9_-]", "", str(participant_id or ""))[:40]
    return _load().get(pid, [])


def extract(text: str) -> Tuple[str, List[str]]:
    found = [m.strip() for m in _ABOUT_LINE.findall(text) if m.strip()]
    if not found:
        return text, []
    cleaned = _ABOUT_LINE.sub("", text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, found[:MAX_PER_MESSAGE]
