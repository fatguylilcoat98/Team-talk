"""
surfacer/tests/test_matcher_v2.py — the recall extension, proven to lift recall
WITHOUT touching precision.

Run:  python3 surfacer/tests/test_matcher_v2.py

The whole point of alias-anchoring is that it is a strict superset of the frozen
matcher: with no aliases it behaves identically (so v1's 19 tests still hold),
and with an alias it catches the loose phrasing the baseline showed we miss
("the m=91 bound") while STILL refusing every frozen non-match.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # surfacer/

import matcher
import matcher_v2 as v2

PASSED = []


def check(cond, name):
    if not cond:
        print(f"  ✗ FAIL: {name}")
        raise SystemExit(1)
    PASSED.append(name)
    print(f"  ✓ {name}")


SEED = [("Hercher", 91), ("de Grey", 1581), ("Heule", 150)]

# The frozen matches / non-matches, verbatim from matcher.py's suite.
REQUIRED_MATCHES = [
    ("Hercher m=91", ("hercher", "91")),
    ("de Grey 1581", ("de grey", "1581")),
    ("I think Hercher's bound is around 91", ("hercher", "91")),
    ("Heule ~150", ("heule", "150")),
]
REQUIRED_NONMATCHES = [
    "Round 10", "Page 227", "Session 59", "de Grey's 2018 result",
    "took 91 minutes to read", "Hercher joined the project in 1991",
    "Hercher wrote 391 pages", "de Grey cited entry 15810 in the appendix",
    "the Herchersmith account has 91 followers",
]


def run():
    # 1. With NO aliases, v2 is identical to v1 on every frozen case.
    r1 = matcher.Registry(SEED)
    r2 = v2.AliasRegistry(SEED)
    for text, _ in REQUIRED_MATCHES:
        check(r2.extract(text) == r1.extract(text), f"1 v2==v1 (match) · {text[:24]!r}")
    for text in REQUIRED_NONMATCHES:
        check(r2.extract(text) == set() == r1.extract(text), f"1 v2==v1 (nonmatch) · {text[:24]!r}")

    # 2. THE RECALL GAIN. "the m=91 bound" has no "Hercher" next to the value —
    #    v1 misses it; v2 with the curated alias "m=91" catches it.
    loose = "the m=91 bound is still the tightest we have"
    check(matcher.Registry(SEED).extract(loose) == set(), "2 v1 MISSES 'the m=91 bound' (the measured blind spot)")
    ra = v2.AliasRegistry(SEED, aliases=[("Hercher", 91, "m=91")])
    check(("hercher", "91") in ra.extract(loose), "2b v2 CATCHES 'the m=91 bound' via the m=91 alias")

    # 3. PRECISION PRESERVED. With that alias loaded, every frozen non-match
    #    STILL refuses to fire — an alias is a specific string, not a bare number.
    for text in REQUIRED_NONMATCHES:
        check(ra.extract(text) == set(), f"3 precision held with alias · {text[:24]!r}")
    # the two number-noise traps that share the value 91 are the important ones
    check(ra.extract("took 91 minutes to read") == set(), "3b 'took 91 minutes' still inert under alias")
    check(ra.extract("Hercher joined the project in 1991") == set(), "3c '1991' still inert under alias")

    # 4. The frozen required-matches still match under v2 with the alias present.
    for text, claim in REQUIRED_MATCHES:
        check(claim in ra.extract(text), f"4 frozen match still fires · {text[:24]!r}")

    # 5. Hedge scoping still works through v2 (duck-typed on .extract()).
    check(v2.hedged_claims("I think the m=91 bound is around right", ra) == {("hercher", "91")},
          "5 hedge detection works with an alias hit")

    print(f"\nALL {len(PASSED)} MATCHER-V2 TESTS PASS")


if __name__ == "__main__":
    run()
