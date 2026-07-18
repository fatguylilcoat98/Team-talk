"""Confidence Pass — 'Is the certainty of the wording stronger than the
available evidence?'

Compares strong-certainty language (in the author's OWN, non-quoted text) to
the supplied evidence and prior history. Produces no numeric epistemic
probability; detector_confidence only describes the heuristic's own match.
"""

from .base import ReflectionPass
from .. import lexicon as lx

_TOPIC_OVERLAP = 0.5


class ConfidencePass(ReflectionPass):
    name = "confidence"

    def evaluate(self, ctx):
        draft = ctx.draft_text or ""
        unquoted = lx.strip_quoted(draft)              # quoted certainty is not the author's
        markers = lx.CERTAINTY_RE.findall(unquoted)
        warnings = []
        dtoks = lx.tokens(draft)
        receipts = ctx.receipts or []
        on_topic_receipt = next(
            (r for r in receipts if lx.topical(dtoks, lx.tokens(r.get("text", ""))) >= _TOPIC_OVERLAP),
            None)

        # 1. unsupported certainty — strong wording, no supporting receipt
        if markers and on_topic_receipt is None:
            warnings.append(self.warn(
                "yellow", "confidence_exceeds_evidence",
                "Language is stronger than the supplied support "
                f"(e.g. \"{sorted({m.lower().strip() for m in markers})[0]}\").",
                current=lx.excerpt(draft), confidence=0.6,
                markers=sorted({m.lower().strip() for m in markers})[:5]))

        # 2. confidence drift — hedged earlier on this topic, certain now, no
        #    new receipt. Red if the new certainty contradicts a ledger receipt.
        if markers:
            for p in ctx.prior_participations or []:
                ptext = lx.entry_text(p)
                if lx.topical(dtoks, lx.tokens(ptext)) >= _TOPIC_OVERLAP and \
                        lx.certainty_score(lx.strip_quoted(ptext)) < 0:
                    if on_topic_receipt is None:
                        contradicts = lx.has_negation(lx.strip_quoted(ptext)) != \
                            lx.has_negation(unquoted)
                        # a ledger-backed contradiction elevates to red
                        red = any(lx.topical(dtoks, lx.tokens(r.get("text", ""))) >= _TOPIC_OVERLAP
                                  and lx.has_negation(lx.strip_quoted(r.get("text", ""))) !=
                                  lx.has_negation(unquoted) for r in receipts)
                        warnings.append(self.warn(
                            "red" if red else "yellow", "confidence_drift",
                            "You hedged on this earlier and are now more certain, "
                            "without a new receipt on record.",
                            current=lx.excerpt(draft), prior=lx.excerpt(ptext),
                            source=lx.entry_id(p), confidence=0.6,
                            earlier_confidence=lx.certainty_score(ptext)))
                        break
        return warnings
