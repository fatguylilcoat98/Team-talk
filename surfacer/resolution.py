"""
surfacer/resolution.py — Team Talk surfacer, build step 3: SOURCE + CHALLENGE.
Christopher Hughes · Sacramento, CA · the resolution layer the room froze.

Two durable, attributed, hash-chained primitives, plus the resolution-gated
registry-growth gate the room kept calling "test thirteen." All state is
DERIVED from the existing append-only ledger — no new data store.

Design (frozen with the room, session team-talk-a2a0e3ae):
- A CLAIM is (entity, value). A FLAG is (seat, entity, value). A SOURCE names a
  CLAIM and clears it room-wide — global-with-attribution (vote was 4-1 for).
- SOURCE: a durable, attributed, hash-chained ledger event with the sourcer's
  name on it forever. It resolves a claim.
- CHALLENGE: any seat can post one against a SOURCE. It reopens that claim
  room-wide and sits in the ledger with the challenger's name until a NEW
  source settles it. No oracle, no clock tiebreaker (Gemini retracted
  "newer=better"): a challenged claim stays reopened until a fresh SOURCE lands.
- REGISTRY GROWTH GATE ("test thirteen"): the tier-two registry grows ONLY for
  claims whose current state is SOURCED — never on repetition, never on
  retraction, and NOT while a source stands challenged. This is the load-bearing
  gate; tests/test_resolution.py executes it.
- ANTI-ABUSE (visible metrics, NOT gates — Grok's red-team, Claude's junk-
  CHALLENGE catch, Gemini's addition): overturned-challenge-rate (a challenge a
  later source overrode is noise on the challenger) and source-count per seat (a
  seat that rides others' sources shows zero). Both visible to all; neither
  blocks anything.

The markers are frozen syntax. Wiring them into the live message flow (the
non-blocking pre-post hook) is the SEPARATE next step and is intentionally NOT
done here — per the room's rule, SOURCE/CHALLENGE freeze with tests run in the
open BEFORE any hook goes live.

Marker syntax (each on its own line in a seat's message):
    SOURCE: <entity>=<value> | <evidence text>
    CHALLENGE: <source_id> | <reason text>
"""

import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _HERE)
sys.path.insert(0, _ROOT)

import matcher   # frozen; imported, never modified
import ledger

SOURCE_ACTION = "surfacer_source"
CHALLENGE_ACTION = "surfacer_challenge"

_SOURCE_LINE = re.compile(
    r"^[ \t]*SOURCE:[ \t]*(.+?)[ \t]*=[ \t]*([^|\n]+?)[ \t]*(?:\|[ \t]*(.*))?$",
    re.MULTILINE)
_CHALLENGE_LINE = re.compile(
    r"^[ \t]*CHALLENGE:[ \t]*(src_[a-f0-9]{6,})[ \t]*(?:\|[ \t]*(.*))?$",
    re.MULTILINE)


# --------------------------------------------------------------- claim identity
def claim_key(entity, value):
    """A CLAIM is (normalized_entity, value-as-string). No seat — a SOURCE
    clears the claim room-wide, so the seat is not part of claim identity."""
    return (matcher.normalize_extraction(entity), str(value).strip())


def source_id(event):
    """A source's id is derived from its own ledger hash, so it can always be
    recomputed when the chain is replayed — nothing extra to store."""
    return "src_" + (event.get("hash") or "")[:12]


# --------------------------------------------------------------- write (durable)
def record_source(seat, entity, value, evidence=""):
    """Append a durable, attributed, hash-chained SOURCE. Returns its id."""
    ent, val = claim_key(entity, value)
    ev = ledger.append(seat, SOURCE_ACTION, ref=f"{ent}={val}",
                       detail={"entity": ent, "value": val, "by": seat,
                               "evidence": (evidence or "").strip()[:500]})
    return source_id(ev)


def record_challenge(seat, target_source_id, reason=""):
    """Append a durable, attributed, hash-chained CHALLENGE against a SOURCE."""
    ev = ledger.append(seat, CHALLENGE_ACTION, ref=str(target_source_id)[:40],
                       detail={"target_source": str(target_source_id)[:40],
                               "by": seat, "reason": (reason or "").strip()[:500]})
    return source_id(ev)


# --------------------------------------------------------------- read (derived)
def _events():
    return [e for e in ledger._read_all() if not e.get("_corrupt")]


def claim_states():
    """Replay the ledger into {claim_key: state}. state is one of
    OPEN / SOURCED / CHALLENGED, with sourced_by, the standing challenge (if
    any), and the list of challenges a later source overturned."""
    evs = _events()
    # index sources by id so a challenge can find the claim it targets
    src_by_id = {}
    for e in evs:
        if e.get("action") == SOURCE_ACTION:
            d = e.get("detail", {})
            src_by_id[source_id(e)] = {"claim": (d.get("entity"), d.get("value")),
                                       "by": d.get("by") or e.get("actor")}
    # per-claim timeline
    timelines = {}
    for e in evs:
        act = e.get("action")
        if act == SOURCE_ACTION:
            d = e.get("detail", {})
            ck = (d.get("entity"), d.get("value"))
            timelines.setdefault(ck, []).append(
                {"seq": e.get("seq", 0), "kind": "SOURCE",
                 "sid": source_id(e), "by": d.get("by") or e.get("actor")})
        elif act == CHALLENGE_ACTION:
            d = e.get("detail", {})
            target = d.get("target_source") or e.get("ref")
            src = src_by_id.get(target)
            if not src:
                continue   # a CHALLENGE against an unknown source is inert
            timelines.setdefault(src["claim"], []).append(
                {"seq": e.get("seq", 0), "kind": "CHALLENGE",
                 "target": target, "by": d.get("by") or e.get("actor")})
    states = {}
    for ck, tl in timelines.items():
        tl.sort(key=lambda x: x["seq"])
        state, sourced_by, standing, overturned = "OPEN", None, None, []
        for ev in tl:
            if ev["kind"] == "SOURCE":
                if standing is not None:      # a fresh source settles past a challenge
                    overturned.append(standing)
                    standing = None
                state, sourced_by = "SOURCED", ev["by"]
            else:                              # CHALLENGE reopens the claim
                state, standing = "CHALLENGED", ev
        states[ck] = {"state": state, "sourced_by": sourced_by,
                      "standing_challenge": standing, "overturned_challenges": overturned}
    return states


def resolved_registry(seed_pairs=None):
    """THE GROWTH GATE. Start from the frozen seed, then admit a claim to the
    tier-two registry ONLY if its current ledger state is SOURCED. Repetition
    never grows it; a standing CHALLENGE holds it out; retraction never grows
    it. Returns a matcher.Registry."""
    reg = matcher.Registry(seed_pairs or [])
    for (ent, val), st in claim_states().items():
        if st["state"] == "SOURCED":
            reg.add(ent, val)
    return reg


# --------------------------------------------------------------- anti-abuse view
def source_count_per_seat():
    counts = {}
    for e in _events():
        if e.get("action") == SOURCE_ACTION:
            by = e.get("detail", {}).get("by") or e.get("actor")
            counts[by] = counts.get(by, 0) + 1
    return counts


def overturned_challenge_rate():
    """Per seat: {challenges, overturned, overturned_rate}. A high rate flags a
    serial challenger whose challenges never survive — Claude's junk-CHALLENGE
    catch, made visible instead of blocked."""
    total, overturned = {}, {}
    for e in _events():
        if e.get("action") == CHALLENGE_ACTION:
            by = e.get("detail", {}).get("by") or e.get("actor")
            total[by] = total.get(by, 0) + 1
    for st in claim_states().values():
        for ch in st["overturned_challenges"]:
            by = ch["by"]
            overturned[by] = overturned.get(by, 0) + 1
    return {seat: {"challenges": total[seat], "overturned": overturned.get(seat, 0),
                   "overturned_rate": round(overturned.get(seat, 0) / total[seat], 4)}
            for seat in total}


# --------------------------------------------------------------- marker parsing
def extract(text):
    """Parse SOURCE:/CHALLENGE: markers out of a seat message. Returns
    (cleaned_text, actions) with actions = {sources: [(entity, value, evidence)],
    challenges: [(source_id, reason)]}. This ONLY parses — the live hook (next
    step) is what calls record_source/record_challenge on the result."""
    t = text or ""
    sources = [(m.group(1).strip(), m.group(2).strip(), (m.group(3) or "").strip())
               for m in _SOURCE_LINE.finditer(t)]
    challenges = [(m.group(1), (m.group(2) or "").strip())
                  for m in _CHALLENGE_LINE.finditer(t)]
    cleaned = _CHALLENGE_LINE.sub("", _SOURCE_LINE.sub("", t))
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, {"sources": sources, "challenges": challenges}


def has_markers(text):
    _, a = extract(text)
    return bool(a["sources"]) or bool(a["challenges"])
