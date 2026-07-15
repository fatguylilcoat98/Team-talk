"""
surfacer/matcher_v2.py — recall extension of the frozen matcher.

The baseline (60 sessions, 1,050 posts) measured the frozen matcher's blind
spot: "the m=91 bound" travels without "Hercher" next to it, so hercher=91
scored ~0 hits even though the room demonstrably repeats it. High precision,
low recall — the "it only sees what it can parse" hole, quantified.

This is the frozen-change-protocol response. matcher.py is UNTOUCHED — its 19
tests still define v1. v2 is purely ADDITIVE: a claim may carry ALIASES —
compact, curated anchor strings like "m=91" — that also trigger a match. With
NO aliases, v2 is identical to v1, so every frozen non-match still refuses to
fire. Aliases lift recall only for the specific loose phrasings the room chooses
to anchor, through the same human/SOURCE-gated curation path — they never
auto-grow, and because an alias is a specific string (not a bare number) it
cannot re-open the precision holes ("took 91 minutes", "1991", "391 pages").

Per the protocol: run the baseline under BOTH v1 and v2 and report the diff.
This file stands beside v1; it does not replace it.
"""
import re

import matcher   # frozen v1 — normalizers/helpers reused unchanged, never modified

# Re-export the frozen pieces so callers can depend on one module.
normalize_identity = matcher.normalize_identity
normalize_extraction = matcher.normalize_extraction
tier1_match = matcher.tier1_match
hedged_claims = matcher.hedged_claims        # duck-typed on .extract(); works with AliasRegistry
HEDGE_MARKERS = matcher.HEDGE_MARKERS


class AliasRegistry:
    """v1 Registry semantics PLUS optional per-claim aliases.

    A claim (entity, value) matches text when EITHER:
      - entity AND value co-occur (the frozen v1 rule), OR
      - any of the claim's alias strings occurs (word-boundary).
    All matching is word-boundary on the extraction-normalized text, so
    substrings never fire. Same public shape as matcher.Registry, so it drops
    into the baseline unchanged."""

    def __init__(self, pairs=None, aliases=None):
        self._reg = {}       # entity(normalized) -> set(value strings)
        self._alias = {}     # alias(normalized) -> set((entity, value))
        for entity, value in (pairs or []):
            self.add(entity, value)
        for entry in (aliases or []):
            self.add_alias(*entry)          # (entity, value, alias)

    def add(self, entity, value):
        self._reg.setdefault(normalize_extraction(entity), set()).add(str(value))

    def add_alias(self, entity, value, alias):
        e, v = normalize_extraction(entity), str(value)
        self.add(e, v)
        norm = normalize_extraction(alias)
        if norm:
            self._alias.setdefault(norm, set()).add((e, v))

    def pairs(self):
        return [(e, v) for e, vals in self._reg.items() for v in vals]

    def aliases(self):
        return [(e, v, a) for a, claims in self._alias.items() for (e, v) in claims]

    def extract(self, text):
        norm = normalize_extraction(text)
        found = set()
        # v1 rule: entity + value co-occur
        for entity, values in self._reg.items():
            if not re.search(rf"\b{re.escape(entity)}\b", norm):
                continue
            for value in values:
                if re.search(rf"\b{re.escape(value)}\b", norm):
                    found.add((entity, value))
        # v2 rule: a curated alias anchor occurs (the value can travel without
        # its entity — "the m=91 bound" — but only via a string the room chose)
        for alias, claims in self._alias.items():
            if re.search(rf"\b{re.escape(alias)}\b", norm):
                found |= claims
        return found
