"""Episodic memory — compressed summaries of aged-out conversation rounds.

Team Talk shows the last SHORT_TERM_ROUNDS rounds verbatim; older rounds
used to just fall off. Now they get compressed (Splendor's Layer 2/4) into
episodes stored here on the server's disk: memory/episodes.json. Episodes
from the current session are injected where the old rounds used to be, and
the most relevant episodes from past sessions ride along with long-term
memory.
"""

import json
import os
import uuid
from datetime import datetime, timezone
from typing import List, Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
EPISODES_DIR = os.path.join(BASE_DIR, "memory")
EPISODES_PATH = os.path.join(EPISODES_DIR, "episodes.json")

MAX_EPISODES = 400
CHUNK_ROUNDS = 6   # max rounds compressed per episode
MIN_CHUNK = 3      # wait until this many rounds have aged out (batches the
                   # summarization instead of one tiny episode per round)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load() -> List[dict]:
    try:
        with open(EPISODES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def _save(episodes: List[dict]) -> None:
    os.makedirs(EPISODES_DIR, mode=0o700, exist_ok=True)
    tmp = f"{EPISODES_PATH}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(episodes[-MAX_EPISODES:], f, indent=2, ensure_ascii=False)
    os.replace(tmp, EPISODES_PATH)


def list_episodes() -> List[dict]:
    return _load()


def for_session(session_id: str) -> List[dict]:
    return [e for e in _load() if e.get("session_id") == session_id]


def covered_until(session_id: str) -> int:
    """Highest round number already compressed for this session (0 = none)."""
    rounds = [e.get("last_round", 0) for e in _load()
              if e.get("session_id") == session_id]
    return max(rounds) if rounds else 0


def add(session_id: str, first_round: int, last_round: int, summary: str) -> dict:
    episode = {
        "id": uuid.uuid4().hex[:12],
        "session_id": session_id,
        "first_round": first_round,
        "last_round": last_round,
        "summary": summary.strip(),
        "created_at": _now(),
    }
    episodes = _load()
    episodes.append(episode)
    _save(episodes)
    return episode


def delete(episode_id: str) -> bool:
    episodes = _load()
    kept = [e for e in episodes if e.get("id") != episode_id]
    if len(kept) == len(episodes):
        return False
    _save(kept)
    return True


def pending_chunk(session_id: str, rounds: List[dict], keep_recent: int) -> Optional[List[dict]]:
    """The next block of aged-out rounds that still needs compression.

    Returns a chunk of up to CHUNK_ROUNDS rounds that (a) have fallen out
    of the verbatim window and (b) aren't covered by an episode yet — or
    None when compression is fully caught up. One chunk per call keeps the
    background work small.
    """
    aged_out = rounds[:-keep_recent] if len(rounds) > keep_recent else []
    done = covered_until(session_id)
    todo = [r for r in aged_out if (r.get("round") or 0) > done]
    if len(todo) < MIN_CHUNK:
        return None
    return todo[:CHUNK_ROUNDS]


def session_block(session_id: str) -> str:
    """The compressed-episode section for the CURRENT session's context."""
    episodes = for_session(session_id)
    if not episodes:
        return ""
    lines = ["(Earlier rounds of this conversation, compressed — the details "
             "aged out but this is what happened:)"]
    for e in episodes:
        lines.append(f"- Rounds {e.get('first_round')}–{e.get('last_round')}: {e.get('summary', '')}")
    return "\n".join(lines)


def episodes_block(episodes: List[dict]) -> str:
    """Cross-session episodes section (already ranked by the brain)."""
    if not episodes:
        return ""
    lines = ["=== PAST CONVERSATIONS (compressed episodes relevant to right now) ==="]
    for e in episodes:
        date = e.get("created_at", "")[:10]
        lines.append(f"- [{e.get('session_id', '?')}, {date}] {e.get('summary', '')}")
    return "\n".join(lines)
