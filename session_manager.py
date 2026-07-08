"""Session persistence — each conversation is one JSON file under sessions/.

Every round is written to disk immediately after both AI responses arrive,
so nothing is lost if the process dies.
"""

import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import List, Optional

import aiofiles

SESSIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sessions")

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def valid_id(session_id: str) -> bool:
    # Strict allowlist — session ids become filenames, so no path characters
    return bool(_ID_RE.match(session_id))


def _path(session_id: str) -> str:
    return os.path.join(SESSIONS_DIR, f"{session_id}.json")


def new_session(session_id: Optional[str] = None) -> dict:
    if not session_id:
        session_id = f"team-talk-{uuid.uuid4().hex[:8]}"
    return {"id": session_id, "created_at": _now(), "rounds": []}


async def load_session(session_id: str) -> Optional[dict]:
    if not valid_id(session_id):
        return None
    path = _path(session_id)
    if not os.path.exists(path):
        return None
    async with aiofiles.open(path, "r", encoding="utf-8") as f:
        return json.loads(await f.read())


async def save_session(session: dict) -> None:
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    path = _path(session["id"])
    tmp = f"{path}.tmp"
    async with aiofiles.open(tmp, "w", encoding="utf-8") as f:
        await f.write(json.dumps(session, indent=2, ensure_ascii=False))
    os.replace(tmp, path)


async def list_sessions() -> List[dict]:
    if not os.path.isdir(SESSIONS_DIR):
        return []
    summaries = []
    for name in sorted(os.listdir(SESSIONS_DIR)):
        if not name.endswith(".json"):
            continue
        try:
            async with aiofiles.open(os.path.join(SESSIONS_DIR, name), "r", encoding="utf-8") as f:
                session = json.loads(await f.read())
        except (json.JSONDecodeError, OSError):
            continue
        rounds = session.get("rounds", [])
        summaries.append({
            "id": session.get("id", name[:-5]),
            "created_at": session.get("created_at", ""),
            "rounds": len(rounds),
            "last_message": rounds[-1]["chris_message"] if rounds else "",
        })
    summaries.sort(key=lambda s: s["created_at"], reverse=True)
    return summaries


def delete_session(session_id: str) -> bool:
    if not valid_id(session_id):
        return False
    path = _path(session_id)
    if not os.path.exists(path):
        return False
    os.remove(path)
    return True


def export_markdown(session: dict) -> str:
    lines = [
        f"# Team Talk — {session['id']}",
        "",
        f"Created: {session.get('created_at', 'unknown')}",
        "",
    ]
    for r in session.get("rounds", []):
        lines += [
            "---",
            "",
            f"## Round {r['round']}  ({r.get('timestamp', '')})",
            "",
            f"**Chris:** {r['chris_message']}",
            "",
            f"### Claude  (tokens: {r.get('claude_tokens', '--')})",
            "",
            r["claude_response"],
            "",
            f"### ChatGPT  (tokens: {r.get('chatgpt_tokens', '--')})",
            "",
            r["chatgpt_response"],
            "",
        ]
    return "\n".join(lines)
