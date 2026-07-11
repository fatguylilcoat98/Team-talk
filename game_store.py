"""🚂 The Train — witnessed co-op storytelling on disk.

The game Chris originally wanted to build on the Sacramento→Bakersfield
train, rebuilt with the room's rules. Two player seats, one AI Game
Master, and a CANON LEDGER: every fact the GM establishes about the
world is registered with an id and hash-chained. Fiction requires
invention — the enemy isn't inventing, it's UNREGISTERED inventing.
"I thought we were playing make-believe" can't happen here, because
make-believe is exactly what this is, on the record.

Rules of canon:
- Facts are append-only and hash-chained (same math as the ledger).
- A fact is never edited. A retcon marks it void and says who, when,
  and why — the original text stays readable forever.
- The chain hash covers ts|by|turn|text|prev_hash. Retcon status lives
  OUTSIDE the hash on purpose: voiding a fact must not break the chain
  that proves the fact was once established.

Storage: games/{game_id}.json — one file per game, gitignored.
"""

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import List, Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
GAMES_DIR = os.path.join(BASE_DIR, "games")

GENESIS = "0" * 64
MAX_PLAYERS = 2
MAX_FACTS_PER_TURN = 8

_ID_RE = re.compile(r"^game_[0-9a-f]{8}$")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _path(game_id: str) -> str:
    return os.path.join(GAMES_DIR, f"{game_id}.json")


def valid_id(game_id: str) -> bool:
    return bool(game_id and _ID_RE.match(game_id))


def fact_hash(ts: str, by: str, turn: int, text: str, prev_hash: str) -> str:
    return _sha(f"{ts}|{by}|{turn}|{text}|{prev_hash}")


# --- persistence ----------------------------------------------------------

def load_game(game_id: str) -> Optional[dict]:
    if not valid_id(game_id):
        return None
    try:
        with open(_path(game_id), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def save_game(game: dict) -> None:
    os.makedirs(GAMES_DIR, mode=0o700, exist_ok=True)
    tmp = f"{_path(game['id'])}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(game, f, indent=2, ensure_ascii=False)
    os.replace(tmp, _path(game["id"]))


def list_games() -> List[dict]:
    """Newest-first summaries for the picker."""
    out = []
    try:
        names = os.listdir(GAMES_DIR)
    except OSError:
        return []
    for name in names:
        if not name.endswith(".json"):
            continue
        game = load_game(name[:-5])
        if not game:
            continue
        out.append({
            "id": game["id"],
            "title": game["title"],
            "created": game["created"],
            "players": [p["name"] for p in game["players"]],
            "gm": game["gm"]["name"],
            "turns": len(game["turns"]),
            "facts": sum(1 for fc in game["facts"] if fc.get("status") == "canon"),
        })
    out.sort(key=lambda g: g["created"], reverse=True)
    return out


# --- lifecycle --------------------------------------------------------------

def create_game(title: str, players: List[str], gm_id: str, gm_name: str) -> Optional[dict]:
    title = (title or "").strip()[:120]
    names = [str(n).strip()[:40] for n in players if str(n).strip()][:MAX_PLAYERS]
    if not title or not names or not gm_id:
        return None
    game = {
        "id": f"game_{uuid.uuid4().hex[:8]}",
        "created": _now(),
        "title": title,
        "players": [{"slot": i + 1, "name": n} for i, n in enumerate(names)],
        "gm": {"id": str(gm_id)[:60], "name": str(gm_name)[:60]},
        "status": "open",
        "facts": [],       # the canon ledger — append-only, hash-chained
        "turns": [],
        "pending": {},     # player name -> move text, waiting for the GM
    }
    save_game(game)
    return game


def submit_move(game: dict, player: str, text: str) -> bool:
    text = (text or "").strip()[:2000]
    names = {p["name"] for p in game["players"]}
    if not text or player not in names:
        return False
    game["pending"][player] = {"text": text, "ts": _now()}
    save_game(game)
    return True


# --- canon ------------------------------------------------------------------

def add_fact(game: dict, text: str, by: str, turn: int) -> dict:
    text = (text or "").strip()[:400]
    prev = game["facts"][-1]["hash"] if game["facts"] else GENESIS
    ts = _now()
    h = fact_hash(ts, by, turn, text, prev)
    fact = {
        "id": f"f_{h[:10]}",
        "ts": ts,
        "by": str(by)[:60],
        "turn": turn,
        "text": text,
        "status": "canon",
        "prev_hash": prev,
        "hash": h,
    }
    game["facts"].append(fact)
    return fact


def get_fact(game: dict, fact_id: str) -> Optional[dict]:
    return next((fc for fc in game["facts"] if fc["id"] == fact_id), None)


def retcon(game: dict, fact_id: str, by: str, reason: str,
           replacement: str = "") -> Optional[dict]:
    """Void a fact without erasing it. The original text stays readable;
    the retcon records who, when, why. An optional replacement becomes a
    NEW canon fact so the correction is itself on the chain."""
    fact = get_fact(game, fact_id)
    if not fact or fact.get("status") != "canon":
        return None
    fact["status"] = "retconned"
    fact["retcon"] = {"ts": _now(), "by": str(by)[:60],
                      "reason": (reason or "").strip()[:300]}
    new_fact = None
    if (replacement or "").strip():
        new_fact = add_fact(game, replacement, by, len(game["turns"]))
        fact["retcon"]["replaced_by"] = new_fact["id"]
    save_game(game)
    return {"voided": fact, "replacement": new_fact}


def verify_canon(game: dict) -> dict:
    """Recompute the fact chain. Retcon status is outside the hash — the
    chain proves what was established and in what order, not that nothing
    was ever voided (voids are visible on their face)."""
    prev = GENESIS
    facts = game.get("facts", [])
    for fc in facts:
        expected = fact_hash(fc.get("ts", ""), fc.get("by", ""),
                             fc.get("turn", 0), fc.get("text", ""),
                             fc.get("prev_hash", ""))
        if fc.get("prev_hash") != prev or fc.get("hash") != expected:
            return {"valid": False, "length": len(facts), "first_bad": fc.get("id")}
        prev = fc.get("hash")
    return {"valid": True, "length": len(facts), "first_bad": None}


def canon_ids(game: dict) -> set:
    return {fc["id"] for fc in game["facts"]}


def canon_block(game: dict) -> str:
    """The world record, formatted for the GM's context."""
    if not game["facts"]:
        return ("=== CANON — THE WORLD RECORD ===\n"
                "(empty — nothing about this world has been established yet)")
    lines = ["=== CANON — THE WORLD RECORD ===",
             "Cite these ids like [f_ab12cd34ef] whenever you use an "
             "established fact. VOID facts no longer hold — do not use them."]
    for fc in game["facts"]:
        if fc.get("status") == "canon":
            lines.append(f"[{fc['id']}] (turn {fc['turn']}) {fc['text']}")
        else:
            r = fc.get("retcon", {})
            lines.append(f"[{fc['id']}] VOID — was: \"{fc['text']}\" "
                         f"(retconned by {r.get('by', '?')}: {r.get('reason', '')})")
    return "\n".join(lines)
