"""Assumption Pass — 'What conclusion depends on an unverified assumption?'

The central distinction: an observation may be supported while the CAUSAL
conclusion drawn from it is not. This pass flags strong causal/conclusion
language for which the supplied context holds no matching receipt. It never
invents evidence or alternative explanations.
"""

from .base import ReflectionPass
from .. import lexicon as lx

_SUPPORT_OVERLAP = 0.5


class AssumptionPass(ReflectionPass):
    name = "assumption"

    def evaluate(self, ctx):
        draft = ctx.draft_text or ""
        unquoted = lx.strip_quoted(draft)
        causal_hits = lx.CAUSAL_RE.findall(unquoted)
        if not causal_hits:
            return []

        dtoks = lx.tokens(draft)
        draft_neg = lx.has_negation(unquoted)
        on_topic = [r for r in (ctx.receipts or [])
                    if lx.topical(dtoks, lx.tokens(r.get("text", ""))) >= _SUPPORT_OVERLAP]
        # A receipt SUPPORTS the conclusion only if it shares polarity; an
        # opposite-polarity on-topic receipt is COMPETING evidence, not support.
        if any(lx.has_negation(lx.strip_quoted(r.get("text", ""))) == draft_neg for r in on_topic):
            return []   # a genuinely supporting receipt covers this conclusion

        competing = next((r for r in on_topic
                          if lx.has_negation(lx.strip_quoted(r.get("text", ""))) != draft_neg), None)

        strong = bool(lx.CERTAINTY_RE.search(unquoted))
        severity = "red" if (strong and competing) else "yellow"
        marker = sorted({h.lower().strip() for h in causal_hits})[:4]
        msg = ("A causal conclusion is drawn that no supplied receipt verifies."
               if severity == "yellow" else
               "A strong causal conclusion is asserted that no supplied receipt "
               "verifies, and supplied evidence points the other way.")
        return [self.warn(
            severity,
            "unsupported_causal_claim" if strong else "unsupported_assumption",
            msg,
            current=lx.excerpt(draft),
            prior=lx.excerpt(competing.get("text", "")) if competing else "",
            source=(competing.get("id") if competing else None),
            confidence=0.55 if severity == "yellow" else 0.75,
            causal_markers=marker)]
