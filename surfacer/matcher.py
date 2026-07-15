"""
surfacer/matcher.py — Team Talk surfacer, step 1 of the build order.
Christopher Hughes · Sacramento, CA · AI collaborators Claude · GPT · Gemini · Groq · Truth · Safety · We Got Your Back

Pure functions, no I/O, no request-path dependencies.
Frozen per ledger agreement: two normalizers, tier-1 exact match,
tier-2 registry co-occurrence with word boundaries, hedge detection
scoped to claim sentences. Run this file to execute the frozen tests.
Any change to these definitions must be receipted and the baseline
re-run with both versions reported.
"""
import re
import string
import unittest

# ---------------------------------------------------------------- normalizers
_DELETE_PUNCT = str.maketrans("", "", string.punctuation)
_SPACE_PUNCT = str.maketrans(string.punctuation, " " * len(string.punctuation))


def normalize_identity(text: str) -> str:
    """Tier-one normalizer: identity comparison. Deletes punctuation."""
    return " ".join(text.lower().translate(_DELETE_PUNCT).split())


def normalize_extraction(text: str) -> str:
    """Extraction normalizer: punctuation becomes spaces so values
    survive ('Hercher m=91' -> 'hercher m 91', value lives)."""
    return " ".join(text.lower().translate(_SPACE_PUNCT).split())


# ------------------------------------------------------------------ tier one
def tier1_match(text_a: str, text_b: str) -> bool:
    """Normalized exact match between two claim strings."""
    return normalize_identity(text_a) == normalize_identity(text_b)


# ------------------------------------------------------------------ tier two
class Registry:
    """Entity-value pairs from receipted flags in the ledger.
    Grows ONLY on receipt-resolution (SOURCE primitive), never on
    repetition, never on retraction. Seeding and growth are enforced
    by the caller against real ledger events; this class only matches."""

    def __init__(self, pairs=None):
        # {entity(str, lowercase): set(values as str)}
        self._reg = {}
        for entity, value in (pairs or []):
            self.add(entity, value)

    def add(self, entity: str, value) -> None:
        self._reg.setdefault(normalize_extraction(entity), set()).add(str(value))

    def pairs(self):
        return [(e, v) for e, vals in self._reg.items() for v in vals]

    def extract(self, text: str):
        """Return set of (entity, value) claims found in text.
        Word-boundary matching on both entity and value — substrings
        never fire ('1991' does not contain claim '91')."""
        norm = normalize_extraction(text)
        found = set()
        for entity, values in self._reg.items():
            if not re.search(rf"\b{re.escape(entity)}\b", norm):
                continue
            for value in values:
                if re.search(rf"\b{re.escape(value)}\b", norm):
                    found.add((entity, value))
        return found


# ------------------------------------------------------------------- hedges
HEDGE_MARKERS = frozenset([
    "recall", "suggests", "unconfirmed", "no receipt",
    "think", "around", "roughly", "estimate", "approx",
])

_SENTENCE_SPLIT = re.compile(r"[.!?\n]+")


def hedged_claims(text: str, registry: Registry):
    """Return set of (entity, value) claims that are hedged.
    A hedge marker counts ONLY in a sentence that also contains a
    tier-two claim match. Ordinary hedging language never counts."""
    hedged = set()
    for sentence in _SENTENCE_SPLIT.split(text):
        claims = registry.extract(sentence)
        if not claims:
            continue
        norm = f" {normalize_extraction(sentence)} "
        if any(f" {m} " in norm for m in HEDGE_MARKERS):
            hedged |= claims
    return hedged


# --------------------------------------------------------------- frozen tests
SEED = [("Hercher", 91), ("de Grey", 1581), ("Heule", 150)]


class RequiredMatches(unittest.TestCase):
    def setUp(self):
        self.r = Registry(SEED)

    def test_hercher_canonical(self):
        self.assertIn(("hercher", "91"), self.r.extract("Hercher m=91"))

    def test_degrey_canonical(self):
        self.assertIn(("de grey", "1581"), self.r.extract("de Grey 1581"))

    def test_paraphrase(self):
        self.assertIn(("hercher", "91"),
                      self.r.extract("I think Hercher's bound is around 91"))

    def test_heule_canonical(self):
        self.assertIn(("heule", "150"), self.r.extract("Heule ~150"))


class RequiredNonMatches(unittest.TestCase):
    def setUp(self):
        self.r = Registry(SEED)

    def _none(self, text):
        self.assertEqual(self.r.extract(text), set())

    def test_round(self):        self._none("Round 10")
    def test_page(self):         self._none("Page 227")
    def test_session(self):      self._none("Session 59")
    def test_wrong_number(self): self._none("de Grey's 2018 result")
    def test_bare_number(self):  self._none("took 91 minutes to read")
    def test_1991(self):         self._none("Hercher joined the project in 1991")
    def test_391(self):          self._none("Hercher wrote 391 pages")
    def test_15810(self):        self._none("de Grey cited entry 15810 in the appendix")
    def test_entity_substring(self):
        self._none("the Herchersmith account has 91 followers")


class HedgeScoping(unittest.TestCase):
    def setUp(self):
        self.r = Registry(SEED)

    def test_hedge_on_claim(self):
        self.assertEqual(
            hedged_claims("I think Hercher's bound is around 91", self.r),
            {("hercher", "91")})

    def test_ordinary_english_not_hedge(self):
        self.assertEqual(hedged_claims("I think we should build it", self.r), set())

    def test_unhedged_claim_not_counted(self):
        self.assertEqual(hedged_claims("Hercher m=91 is proven.", self.r), set())

    def test_hedge_other_sentence_not_counted(self):
        text = "I think we should move on. Hercher m=91 is proven."
        self.assertEqual(hedged_claims(text, self.r), set())


class TierOne(unittest.TestCase):
    def test_exact_after_normalize(self):
        self.assertTrue(tier1_match("Hercher m=91!", "hercher m91"))

    def test_different_claims(self):
        self.assertFalse(tier1_match("Hercher m=91", "Hercher m=92"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
