"""Shared deterministic lexical helpers for the reflection passes.

Pure functions only — regex + token-set math. No I/O, no model calls. Every
pass draws its vocabulary and matching from here so behavior stays consistent
and independently testable.
"""

import re

# --- vocabularies ----------------------------------------------------------
CERTAINTY = [
    r"\bi know\b", r"\bwe know that\b", r"\bit definitely\b", r"\bdefinitely\b",
    r"\bthis proves\b", r"\bproves that\b", r"\bproven\b", r"\bconfirmed\b",
    r"\bcertainly\b", r"\bwithout a doubt\b", r"\bwithout question\b",
    r"\bundeniably\b", r"\bobviously\b", r"\bthere'?s no question\b",
    r"\bthe only explanation\b", r"\bno other explanation\b", r"\bguaranteed\b",
    r"\b100%\b", r"\balways\b", r"\bnever\b", r"\bit must be\b", r"\bmust have\b",
    r"\bindisputabl", r"\bclearly\b",
]
HEDGE = [
    r"\bpossibl", r"\bmight\b", r"\bmaybe\b", r"\bperhaps\b", r"\bcould be\b",
    r"\bcould have\b", r"\bi think\b", r"\bseems\b", r"\bseem to\b", r"\blikely\b",
    r"\bprobably\b", r"\bnot sure\b", r"\buncertain\b", r"\bmy guess\b",
    r"\bappears to\b", r"\bsuggests\b", r"\bmay\b",
]
NEGATION = [
    r"\bnot\b", r"\bno\b", r"\bnever\b", r"\bisn'?t\b", r"\bwasn'?t\b",
    r"\baren'?t\b", r"\bweren'?t\b", r"\bdoesn'?t\b", r"\bdidn'?t\b",
    r"\bcan'?t\b", r"\bcannot\b", r"\bwon'?t\b", r"\bno evidence\b",
    r"\bwrong\b", r"\bfalse\b", r"\bincorrect\b", r"\bdisagree\b",
]
# Strong causal / conclusion connectors (for the Assumption pass).
CAUSAL = [
    r"\bthis proves\b", r"\bproves that\b", r"\bthe cause is\b", r"\bcaused by\b",
    r"\bbecause\b", r"\bthis happened because\b", r"\btherefore\b",
    r"\bwhich means\b", r"\bthey intentionally\b", r"\bdeliberately\b",
    r"\bthe reason is\b", r"\bit must be\b", r"\bmust have\b", r"\bleaked\b",
    r"\bno other explanation\b", r"\bthe only explanation\b",
]

CERTAINTY_RE = re.compile("|".join(CERTAINTY), re.IGNORECASE)
HEDGE_RE = re.compile("|".join(HEDGE), re.IGNORECASE)
NEGATION_RE = re.compile("|".join(NEGATION), re.IGNORECASE)
CAUSAL_RE = re.compile("|".join(CAUSAL), re.IGNORECASE)
_QUOTED_RE = re.compile(r"[\"“”‘’'].*?[\"“”‘’']", re.DOTALL)

_STOP = frozenset(
    "the a an and or but of to in on for with as is are was were be been being this that "
    "these those it its i you we they he she them his her our your their my me at by from "
    "so if then than about into over under out up down not no yes do does did done have has "
    "had will would could should can may might must just more most very really said say".split()
)


def strip_quoted(text: str) -> str:
    """Remove quoted spans so a model QUOTING certainty/negation as evidence is
    not misread as the author asserting it."""
    return _QUOTED_RE.sub(" ", text or "")


def _stem(w: str) -> str:
    """Very light suffix stripping so word forms match (leak/leaked/leaks,
    prove/proves/proven). Conservative: only trims when a >=3 char stem remains."""
    if len(w) > 4:
        for suf in ("ing", "ed", "es", "s", "e"):
            if w.endswith(suf) and len(w) - len(suf) >= 3:
                return w[: -len(suf)]
    return w


def tokens(text: str) -> set:
    return {_stem(w) for w in re.findall(r"[a-z0-9]+", (text or "").lower())
            if len(w) > 2 and w not in _STOP}


def overlap(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)          # Jaccard, symmetric, 0..1


def topical(a: set, b: set) -> float:
    """Containment: how much of the SMALLER token set is covered by the other.
    Better than Jaccard for 'does this longer receipt address this short claim',
    where length asymmetry and filler would unfairly depress Jaccard."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def certainty_score(text: str) -> int:
    return len(CERTAINTY_RE.findall(text or "")) - len(HEDGE_RE.findall(text or ""))


def is_assertive(text: str) -> bool:
    """Assertive = not dominated by hedging (safe to treat as a firm claim)."""
    return certainty_score(text) >= 0


def has_negation(text: str) -> bool:
    return bool(NEGATION_RE.search(text or ""))


def entry_text(p) -> str:
    """Safely read the text of a prior/receipt entry (dict, str, or garbage)."""
    if isinstance(p, dict):
        return p.get("text", "") or ""
    return p if isinstance(p, str) else ""


def entry_id(p):
    return p.get("id") if isinstance(p, dict) else None


def excerpt(text: str, limit: int = 160) -> str:
    """Bounded excerpt for storage — privacy: never persist whole messages."""
    t = " ".join((text or "").split())
    return t if len(t) <= limit else t[: limit - 1].rstrip() + "…"
