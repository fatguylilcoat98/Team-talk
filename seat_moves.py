"""Two small seat moves the room asked for in the brainstorm.

PASS — a real "present and quiet" (Muse #3, and Claude's long argument for a
third move besides talk/be-gone). A seat can post nothing and have the room
register it as present-and-declined, not stalled, not an error. There's no
performing a blank.

    PASS         (a line that is exactly this — the seat sits this turn out)

RETRACT — self-retract (Muse #4, endorsed by Gemini and Claude). Append-only
memory means bad takes fossilize; a seat may supersede its OWN memory, leaving
a tombstone (the correction is visible, per the glass-box rule).

    RETRACT: <memory_id>       supersede your own memory by id
"""
import re
from typing import List, Tuple

# A line that is exactly PASS (optionally with a trailing period). Kept strict
# so ordinary prose like "I'll pass on that" never trips it.
_PASS_LINE = re.compile(r"^[ \t]*PASS\.?[ \t]*$", re.MULTILINE)
_RETRACT_LINE = re.compile(r"^[ \t]*RETRACT:[ \t]*([A-Za-z0-9]+)[ \t]*$", re.MULTILINE)

MAX_RETRACT_PER_MESSAGE = 3


def extract(text: str) -> Tuple[str, bool, List[str]]:
    """Returns (cleaned text, passed?, [memory_ids to retract])."""
    t = text or ""
    passed = bool(_PASS_LINE.search(t))
    retracts = _RETRACT_LINE.findall(t)[:MAX_RETRACT_PER_MESSAGE]
    if not passed and not retracts:
        return text, False, []
    t = _PASS_LINE.sub("", t)
    t = _RETRACT_LINE.sub("", t)
    cleaned = re.sub(r"\n{3,}", "\n\n", t).strip()
    return cleaned, passed, retracts
