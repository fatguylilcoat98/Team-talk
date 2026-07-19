"""Experiment-scoped anonymous context — the fix for the cosmetic blind round.

THE DEFECT THIS EXISTS FOR
Auditing the fully constructed blind prompts showed 25 identity leaks: every seat
received every other seat's real name. The system prompt anonymised correctly, but the
CONVERSATION HISTORY did not — in one FLINT prompt, "Gemini" appeared 56 times, "Claude"
32, "ChatGPT" 27, "Grok" 11, all inside the context block. Names were hidden in the
browser while still being sent to the models.

THE APPROACH, AND WHY NOT THE OTHER ONE
There were two ways to fix it:

  (a) Rewrite names inside historical message text.
  (b) Scope the blind context to the experiment itself.

(a) was rejected. Historical messages are the room's record; rewriting "Claude said X"
into "Voice 4 said X" would mean feeding models a doctored transcript, and the room's
whole architecture is built on records that are not rewritten. It also cannot work:
identity is carried by content, not only names — prior rounds discuss who runs a Plant,
who is the local model, who came back with a receipt.

(b) is what this module does. A blind experiment starts a FRESH conversational window.
Only rounds inside the experiment are shown, relabelled to Voice N at render time. The
real history still exists, untouched, in sessions/ — it is simply not in scope for this
experiment. The append-only record is preserved exactly.

WHAT REMAINS THE CALLER'S JOB
This module returns scoped, relabelled rounds. It does not decide whether the result is
clean — identity_guard.audit_prompt() does, on the fully constructed prompt, and a blind
round must fail closed on a leak rather than run.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

#: Chris stays identifiable by default (he is the human, not a blinded subject).
CHRIS_IDS = {"chris", "splendor"}


def scoped_rounds(rounds: List[dict], experiment: dict) -> List[dict]:
    """Only the rounds that belong to this experiment.

    Everything before the experiment opened is OUT OF SCOPE — not rewritten, not
    redacted, simply not shown. The record is untouched; the window is narrowed."""
    if not experiment:
        return rounds
    included = set(experiment.get("rounds") or [])
    if not included:
        return []
    return [r for r in (rounds or []) if r.get("round") in included]


def relabel_round(rnd: dict, mapping: Dict[str, str]) -> dict:
    """Render one round with Voice labels in place of names.

    A COPY is returned. The stored round is never mutated — this is a presentation
    layer, and the session file on disk keeps the real authorship."""
    out = dict(rnd)
    responses = []
    for resp in rnd.get("responses", []) or []:
        r = dict(resp)
        pid = r.get("id")
        label = mapping.get(pid)
        if label:
            r["name"] = label
            r["label"] = label
            r.pop("persona", None)
            r.pop("color", None)
            r.pop("model", None)
            r.pop("provider", None)
        responses.append(r)
    out["responses"] = responses
    return out


def scoped_history(rounds: List[dict], experiment: dict,
                   mapping: Optional[Dict[str, str]] = None) -> List[dict]:
    """The history a blind turn may see: in-experiment rounds, relabelled."""
    mapping = mapping if mapping is not None else (experiment or {}).get("mapping") or {}
    return [relabel_round(r, mapping) for r in scoped_rounds(rounds, experiment)]


# ---- residual identity inside message TEXT -----------------------------------

def scan_round_text(rounds: List[dict], participants: List[dict]) -> List[dict]:
    """Names spoken INSIDE message text, even within the experiment window.

    Relabelling authorship does not touch what a message SAYS. If Chris writes "Claude,
    what do you think?" inside a blind round, or one seat names another, that text
    carries identity into everyone's context. These are reported, never silently
    rewritten — a blind round with residual identity in its text is compromised, and
    the caller must be told rather than reassured."""
    terms = []
    for p in participants or []:
        for key in ("name", "id"):
            v = (p.get(key) or "").strip()
            if len(v) >= 3 and v.lower() not in CHRIS_IDS:
                terms.append(v)
    seen, uniq = set(), []
    for t in terms:
        if t.lower() not in seen:
            seen.add(t.lower())
            uniq.append(t)

    found = []
    for rnd in rounds or []:
        blobs = [("chris_message", rnd.get("chris_message") or "")]
        for resp in rnd.get("responses", []) or []:
            blobs.append((f"response:{resp.get('id')}", resp.get("text") or ""))
        for where, body in blobs:
            for term in uniq:
                for m in re.finditer(rf"(?<![\w-]){re.escape(term)}(?![\w-])", body, re.I):
                    found.append({
                        "kind": "identity_in_round_text",
                        "round": rnd.get("round"),
                        "where": where,
                        "term": term,
                        "excerpt": body[max(0, m.start() - 60):m.end() + 60].replace("\n", " "),
                    })
    return found


def prepare_blind_turn(rounds: List[dict], experiment: dict,
                       participants: List[dict]) -> dict:
    """Everything a blind turn needs, plus an honest verdict on whether it is blind.

    `safe_to_run` is the fail-closed gate: False means identity survives somewhere in
    the scoped window, and the round must not be sent as a blind experiment."""
    mapping = (experiment or {}).get("mapping") or {}
    history = scoped_history(rounds, experiment, mapping)
    residual = scan_round_text(history, participants)
    return {
        "history": history,
        "rounds_in_scope": [r.get("round") for r in history],
        "rounds_excluded": max(0, len(rounds or []) - len(history)),
        "residual_identity": residual,
        "safe_to_run": not residual,
        "note": ("Pre-experiment rounds are out of scope, not rewritten — the stored "
                 "record is unchanged."),
    }
