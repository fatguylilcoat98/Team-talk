"""Identity guard for blind rounds — audit prompts going out, strip headers coming back.

TWO JOBS, IN OPPOSITE DIRECTIONS

1. `audit_prompt()` — BEFORE a blind turn is sent, check the fully constructed prompt
   for any other participant's real identity: display name, participant id, model name,
   or provider. A blind round that ships a prompt naming Claude is not a blind round,
   and the caller is expected to fail closed rather than run it.

2. `strip_identity_header()` — AFTER generation, remove a self-identifying HEADER from
   the reply. This is the measured, live failure: across the archive FLINT opened 21% of
   his messages with "FLINT to the room", "Here's my response as FLINT:" or similar,
   while every other seat did so 0% of the time. In a blind round that publicly un-blinds
   him regardless of the Voice label.

WHY A HEADER STRIP AND NOT A PROMPT CHANGE
FLINT's Plant is frozen for his evaluation phase, and both Team Talk's room prompt and
the Plant's packet end with "write your next message as FLINT" — which is very likely
what trains the preamble. Removing an opening header after the fact is the smallest
change that fixes the leak without touching the frozen Plant or rewriting a single word
of what FLINT actually argued.

THE LINE THIS MODULE WILL NOT CROSS
It only removes a LEADING header — a standalone opener whose whole job is to announce
who is speaking. If an identity appears inside substantive prose ("as I said earlier,
I'm the 7B seat"), it is NOT edited: the round is reported COMPROMISED instead. Silently
rewriting an argument to preserve an experiment would corrupt the evidence the
experiment exists to produce.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

# ---- leak kinds --------------------------------------------------------------

HEADER = "identity_header"            # removable: a leading self-announcement
PROSE = "identity_in_prose"           # NOT removable: compromises the round
PROMPT = "identity_in_prompt"         # outbound: another seat's identity in the prompt

# A leading self-announcement. Anchored to the START of the reply and kept to a single
# short line, so it can never eat into an argument.
_HEADER_PATTERNS = [
    # **FLINT to the room** / FLINT to the room:
    r"^\s*\**\s*(?P<who>[A-Za-z][\w.-]{1,20})\s+(?:to the room|here)\s*\**\s*[:\-—]?\s*$",
    # Here's / Here is / This is my response as FLINT:
    # ("here is" spelled out was missed by an earlier version and shipped a leak.)
    r"^\s*\**\s*(?:here'?s|here is|this is)\s+my\s+(?:response|reply|answer)\s+as\s+(?P<who>[A-Za-z][\w.-]{1,20})\s*\**\s*[:\-—]?\s*$",
    # I'll respond to the room as FLINT, acknowledging their points.
    r"^\s*\**\s*i'?(?:ll|\s+will)\s+(?:respond|reply|answer)\b[^.\n]{0,80}?\bas\s+(?P<who>[A-Za-z][\w.-]{1,20})\b[^.\n]{0,80}[.:]?\s*$",
    # Responding as FLINT:
    r"^\s*\**\s*(?:responding|answering|replying)\s+as\s+(?P<who>[A-Za-z][\w.-]{1,20})\s*\**\s*[:\-—]?\s*$",
    # FLINT: (a bare speaker tag on its own line)
    r"^\s*\**\s*(?P<who>[A-Za-z][\w.-]{1,20})\s*\**\s*:\s*$",
]
_HEADERS = [re.compile(p, re.I) for p in _HEADER_PATTERNS]

#: A header may only be this many lines from the top, so a mid-message line is never cut.
_HEADER_SCAN_LINES = 2

#: SELF-DESCRIPTIVE identity — phrases that identify a seat without naming it.
#: This is not hypothetical: FLINT talks about his own model and machinery in 13% of his
#: archived messages (the highest of any established seat, ~10x ChatGPT and Grok), and
#: everyone in the room knows which seat is the local 7B running a Plant. In a blind
#: round "as the 7B seat here" un-blinds him just as surely as his name does, so these
#: count as prose leaks even though no roster term appears.
SELF_DESCRIPTIVE = [
    r"\b7B\b", r"\b8B\b",
    r"\bllama[\w.-]*\b",
    r"\bthe Plant\b",
    r"\bmy (?:weights|architecture|governance|Plant)\b",
    r"\bas a (?:local|small|open[- ]source) model\b",
    r"\blocal (?:model|seat)\b",
    r"\bFSYS-[A-Z]+\b",          # FLINT's own system facts, cited by id
    r"\bREF-[A-Z]+\b",           # the Plant's fact ids — no other seat emits these
    r"\bgoverned memory\b", r"\bverified fact\b",
]
_SELF_DESC = [re.compile(p, re.I) for p in SELF_DESCRIPTIVE]


def _identity_terms(participants: List[dict], exclude_id: Optional[str] = None) -> List[str]:
    """Every string that would identify a participant: name, id, model, provider."""
    terms: List[str] = []
    for p in participants:
        if exclude_id and p.get("id") == exclude_id:
            continue
        for key in ("name", "id", "model"):
            v = (p.get(key) or "").strip()
            if len(v) >= 3:
                terms.append(v)
        prov = (p.get("provider") or "").strip()
        if len(prov) >= 3:
            terms.append(prov)
        base = (p.get("base_url") or "")
        for vendor in ("openai", "anthropic", "x.ai", "deepseek", "google", "gemini"):
            if vendor in base.lower():
                terms.append(vendor)
    seen, out = set(), []
    for t in terms:
        k = t.lower()
        if k not in seen:
            seen.add(k)
            out.append(t)
    return out


# ---- 1. outbound: audit the constructed prompt -------------------------------

def audit_prompt(prompt: str, participants: List[dict], me_id: str) -> dict:
    """Does this prompt name anyone OTHER than its own recipient?

    The recipient's own identity is allowed and expected — each model keeps its own
    system identity privately. What must never appear is another seat's identity."""
    text = prompt or ""
    low = text.lower()
    leaks = []
    for term in _identity_terms(participants, exclude_id=me_id):
        # Word-boundary match so "grok" does not fire inside an unrelated word.
        if re.search(rf"(?<![\w-]){re.escape(term.lower())}(?![\w-])", low):
            idx = low.find(term.lower())
            leaks.append({
                "kind": PROMPT,
                "term": term,
                "recipient": me_id,
                "excerpt": text[max(0, idx - 60):idx + len(term) + 60].replace("\n", " "),
            })
    return {"clean": not leaks, "leaks": leaks, "recipient": me_id}


def audit_all_prompts(prompts: Dict[str, str], participants: List[dict]) -> dict:
    """Audit every seat's prompt. `clean` is the gate a blind round should fail closed on."""
    results, all_leaks = {}, []
    for pid, prompt in prompts.items():
        r = audit_prompt(prompt, participants, pid)
        results[pid] = r
        all_leaks.extend(r["leaks"])
    return {"clean": not all_leaks, "leaks": all_leaks, "per_seat": results}


# ---- 2. inbound: strip a self-identifying header ------------------------------

def strip_identity_header(text: str, identity_terms: Optional[List[str]] = None) -> dict:
    """Remove a LEADING self-announcement. Returns the text and what was removed.

    Only a header is touched. Substantive prose is returned untouched, always."""
    original = text or ""
    lines = original.split("\n")
    removed: List[str] = []
    idx = 0
    scanned = 0
    while idx < len(lines) and scanned < _HEADER_SCAN_LINES:
        line = lines[idx]
        if not line.strip():           # blank lines before a header don't count
            idx += 1
            continue
        scanned += 1
        matched = None
        for rx in _HEADERS:
            m = rx.match(line)
            if not m:
                continue
            who = (m.groupdict().get("who") or "").lower()
            # If we know the roster, only strip when the header names a participant —
            # so an ordinary sentence that happens to fit the shape survives.
            if identity_terms and who and who not in {t.lower() for t in identity_terms}:
                continue
            matched = line
            break
        if not matched:
            break
        removed.append(matched.strip())
        lines.pop(idx)
        while idx < len(lines) and not lines[idx].strip():
            lines.pop(idx)
    return {
        "text": "\n".join(lines).strip(),
        "removed": removed,
        "changed": bool(removed),
    }


def scan_prose_for_identity(text: str, identity_terms: List[str],
                            include_self_descriptive: bool = True) -> List[dict]:
    """Identity left in substantive prose AFTER the header strip.

    These cannot be removed without rewriting what the seat actually said, so they
    compromise the round instead. Covers both named identity and self-descriptive
    identity ("the 7B seat", a [REF-…] citation only one seat can produce)."""
    body = text or ""
    found = []
    for term in identity_terms or []:
        for m in re.finditer(rf"(?<![\w-]){re.escape(term)}(?![\w-])", body, re.I):
            found.append({
                "kind": PROSE,
                "term": term,
                "excerpt": body[max(0, m.start() - 70):m.end() + 70].replace("\n", " "),
            })
    if include_self_descriptive:
        for rx in _SELF_DESC:
            for m in rx.finditer(body):
                found.append({
                    "kind": PROSE,
                    "term": m.group(0),
                    "self_descriptive": True,
                    "excerpt": body[max(0, m.start() - 70):m.end() + 70].replace("\n", " "),
                })
    return found


def guard_response(text: str, *, me_id: str, participants: List[dict]) -> dict:
    """The full inbound guard for one blind reply.

    Strips a self-identifying header; then reports any identity still present in prose
    as a COMPROMISE rather than editing it away."""
    self_terms = _identity_terms([p for p in participants if p.get("id") == me_id])
    other_terms = _identity_terms(participants, exclude_id=me_id)

    stripped = strip_identity_header(text, identity_terms=self_terms + other_terms)
    prose_leaks = scan_prose_for_identity(stripped["text"], self_terms + other_terms)

    return {
        "text": stripped["text"],
        "header_removed": stripped["removed"],
        "leaks": prose_leaks,
        "compromised": bool(prose_leaks),
        "clean": not prose_leaks,
    }
