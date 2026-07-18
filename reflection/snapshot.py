"""Bounded, read-only reasoning-ledger snapshot for the reflection passes.

The passes evaluate a draft against `receipts` and an `attribution_map`. In
live chat those were empty, so Assumption/Confidence were underfed. This module
produces a SMALL, READ-ONLY snapshot of the reasoning ledger (reasoning_store)
to feed them — and nothing more.

Hard guarantees (see tests/test_reflection_snapshot.py):
  * READ-ONLY — reads reasoning_store; never writes the ledger, sessions, or
    anything else. reasoning_store exposes no mutation to this module anyway.
  * BOUNDED — at most MAX_RECEIPTS receipts; each excerpt capped to EXCERPT.
  * SEAT-SAFE — only seat names and ledger participation ids/excerpts; no
    cross-seat private data, no full transcripts.
  * NO SECRETS — never touches .env, settings, API keys, or provider prompts.
  * NO HIDDEN REASONING — participation *content* is public room text; no
    chain-of-thought is read or stored.
  * DEGRADES SAFELY — any failure or missing ledger returns an empty snapshot,
    so the passes simply run with limited input rather than breaking.
"""

from .lexicon import excerpt

MAX_RECEIPTS = 25          # hard cap on receipts fed to the passes
EXCERPT = 160              # per-receipt / per-phrase excerpt cap
_ATTR_PHRASE = 80          # attribution phrase cap


def ledger_snapshot(max_receipts: int = MAX_RECEIPTS) -> dict:
    """Return {"receipts": [{id, text}], "attribution_map": {phrase: seat}}.

    Receipts are the most recent reasoning-ledger participations (bounded, as
    short excerpts) — the passes match them topically, so unrelated entries
    simply don't match. The attribution map keys a bounded participation
    excerpt to the seat the ledger records as its author (explicit ledger data,
    never inferred from the draft). Empty on any failure.
    """
    try:
        import reasoning_store   # local import so the layer never hard-depends on it
        parts = [p for p in reasoning_store.list_participations()
                 if isinstance(p, dict) and not p.get("_corrupt")]
    except Exception:
        return {"receipts": [], "attribution_map": {}}

    parts = parts[-max(1, min(max_receipts, MAX_RECEIPTS)):]
    receipts, attribution = [], {}
    for p in parts:
        pid = p.get("participation_id")
        content = p.get("content", "") or ""
        if not content.strip():
            continue
        receipts.append({"id": pid, "text": excerpt(content, EXCERPT)})
        seat = p.get("seat")
        # explicit ledger attribution: the participation's own bounded text ->
        # its recorded seat. Bounded phrases rarely substring-match a fresh
        # draft, so this is conservative and never guesses authorship.
        phrase = excerpt(content, _ATTR_PHRASE)
        if seat and phrase and phrase not in attribution:
            attribution[phrase] = seat
    return {"receipts": receipts, "attribution_map": attribution}
