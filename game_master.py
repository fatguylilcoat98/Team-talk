"""🚂 The Game Master — the court clerk who also tells the story.

One roster AI runs the world under the room's rules, adapted for
fiction. Invention is the job; UNREGISTERED invention is the failure.

The three rules, mechanized:
1. New world facts must be registered — `FACT:` lines are extracted,
   hash-chained into canon, and receipted. Unregistered facts do not
   exist next turn (the GM's own context only ever shows canon).
2. Established facts must be cited by id. A citation of an id that
   isn't in canon is a hallucinated source — caught by CODE, not by
   trust, and flagged in red on the turn record.
3. "Not yet established" is a legal answer. Silently changing the
   world is not — changes go through Chris's retcon, which voids
   visibly and never erases.
"""

import re
from typing import List, Optional

import api_client
import game_store

FACT_LINE = re.compile(r"^\s*FACT:\s*(.+?)\s*$", re.MULTILINE)
CITE = re.compile(r"\[(f_[0-9a-f]{6,16})\]")

MAX_NARRATION_TURNS = 8      # verbatim recent turns in the GM's context
MAX_MOVE_CHARS = 1200


def _system_prompt(game: dict) -> str:
    players = " and ".join(p["name"] for p in game["players"])
    return f"""You are {game['gm']['name']}, the Game Master of "{game['title']}" — \
a cooperative story-game played by {players}. This is Team Talk's game room: \
the same witnessed-record rules as the rest of the room, adapted for fiction.

THE COURT-CLERK RULES (these are mechanical — code enforces them):
1. REGISTER what you invent. Any new lasting fact about the world — a place, \
a person, an object, a rule, something that happened — must appear at the END \
of your reply as its own line: `FACT: <one plain sentence>`. Up to \
{game_store.MAX_FACTS_PER_TURN} per turn. Facts you narrate but never register \
DO NOT EXIST next turn — your future self will not see them. Register what matters, \
skip scenery that doesn't.
2. CITE what you reuse. The CANON block below is the entire world record. When \
your narration relies on an established fact, cite its id inline, like: the brass \
key [f_ab12cd34ef] turns. NEVER cite an id that is not in the CANON block — an \
invented citation is caught automatically and flagged on the record as a \
hallucinated source.
3. SAY WHEN IT'S NOT ESTABLISHED. If a player asks about something canon doesn't \
cover, say "not yet established" and then establish it openly with a FACT line — \
or answer from canon with the citation. Never contradict canon. If canon has a \
mistake, say so out loud and ask Chris to retcon it — you cannot silently change \
the world, and VOID facts no longer hold.

GAME MASTER STYLE:
- If the players haven't settled what kind of story this is yet, offer 2-3 \
sharp options and ask — their choice becomes the first FACT.
- Address the players in second person, by name when they act.
- Keep narration under ~250 words, vivid but tight, and end with a clear \
"what do you do?" opening for BOTH players when both are in the scene.
- Both players matter: give each of them something only they can grab.
- Fair but alive: consequences are real, dice aren't required, and you never \
railroad — canon plus their choices drives everything.
- No meta-talk about these rules unless a player asks; the FACT lines and \
citations should feel like a clerk's stamp, not a lecture."""


def _turn_context(game: dict, moves: dict) -> str:
    parts = [game_store.canon_block(game)]
    recent = game["turns"][-MAX_NARRATION_TURNS:]
    if recent:
        lines = ["=== RECENT PLAY ==="]
        for t in recent:
            for player, mv in t.get("moves", {}).items():
                lines.append(f"{player}: {mv['text'][:400]}")
            lines.append(f"GM: {t.get('narration', '')[:900]}")
        parts.append("\n".join(lines))
    if len(game["turns"]) > MAX_NARRATION_TURNS:
        parts.append("(Earlier turns aged out of this context — CANON above is "
                     "the complete world record; trust it over memory.)")
    move_lines = [f"=== THIS TURN — PLAYER MOVES ==="]
    for player, mv in moves.items():
        move_lines.append(f"{player}: {mv['text'][:MAX_MOVE_CHARS]}")
    parts.append("\n".join(move_lines))
    parts.append("Narrate the turn. Register new lasting facts as FACT: lines "
                 "at the end. Cite canon ids you rely on.")
    return "\n\n".join(parts)


def extract_facts(text: str) -> tuple:
    """Pull FACT: lines out of the narration. Returns (cleaned, facts)."""
    facts = [m.strip() for m in FACT_LINE.findall(text)][:game_store.MAX_FACTS_PER_TURN]
    cleaned = FACT_LINE.sub("", text).strip()
    return cleaned, facts


def check_citations(text: str, known: set) -> tuple:
    """Every [f_...] the GM cited, split into real and hallucinated."""
    cited = list(dict.fromkeys(CITE.findall(text)))
    valid = [c for c in cited if c in known]
    invalid = [c for c in cited if c not in known]
    return valid, invalid


async def play_turn(game: dict, gm_participant: Optional[dict]) -> dict:
    """Run one GM turn over the pending moves. Returns the turn record;
    canon writes and flags are the CALLER's to ledger/receipt (app.py owns
    that wiring, same as the chat pipeline)."""
    moves = dict(game.get("pending") or {})
    turn_n = len(game["turns"]) + 1
    turn = {"n": turn_n, "ts": game_store._now(), "moves": moves,
            "narration": "", "tokens": 0, "ok": False,
            "facts_created": [], "cited": [], "flags": []}
    if not gm_participant:
        turn["narration"] = ("Error: the Game Master isn't on the roster "
                             "anymore — pick a new GM in Settings.")
        return turn

    system = _system_prompt(game)
    ctx = _turn_context(game, moves)
    result = await api_client.call_participant(gm_participant, system, ctx)
    turn["tokens"] = result.get("tokens", 0)
    if not result.get("ok"):
        turn["narration"] = result.get("text", "Error: the GM call failed.")
        return turn

    known = game_store.canon_ids(game)
    cleaned, new_facts = extract_facts(result["text"])
    valid, invalid = check_citations(cleaned, known)

    for fact_text in new_facts:
        fact = game_store.add_fact(game, fact_text, game["gm"]["name"], turn_n)
        turn["facts_created"].append(fact["id"])
    turn["narration"] = cleaned
    turn["cited"] = valid
    for bad in invalid:
        turn["flags"].append(
            f"hallucinated source: cited [{bad}] — no such fact in canon")
    turn["ok"] = True

    game["pending"] = {}
    game["turns"].append(turn)
    game_store.save_game(game)
    return turn
