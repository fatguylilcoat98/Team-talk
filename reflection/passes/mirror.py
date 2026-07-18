"""Mirror Pass — 'Have I previously said/flagged/learned something that
conflicts with this draft?'

Conservative. Reads only the author's own supplied prior participations,
receipts, and (optional) attribution map. Never guesses authorship from prose,
never treats quoted text as the author's own claim.
"""

from .base import ReflectionPass
from .. import lexicon as lx

_CONTRA_OVERLAP = 0.45
_REPEAT_OVERLAP = 0.5
_REPEAT_MIN = 2
_NAME_RE = None  # built lazily below


class MirrorPass(ReflectionPass):
    name = "mirror"

    def evaluate(self, ctx):
        warnings = []
        draft = ctx.draft_text or ""
        # topic + polarity are judged on the author's OWN (unquoted) words, so
        # quoting another seat's claim never counts as the author's own.
        draft_unquoted = lx.strip_quoted(draft)
        dtoks = lx.tokens(draft_unquoted)
        draft_neg = lx.has_negation(draft_unquoted)
        draft_assertive = lx.is_assertive(draft_unquoted)

        receipts_tok = [(r.get("id"), lx.tokens(r.get("text", "")), r) for r in (ctx.receipts or [])]

        # 1. prior contradiction (red) — a firm prior claim on the same topic
        #    with the opposite polarity. Both sides must be assertive.
        if draft_assertive:
            for p in ctx.prior_participations or []:
                ptext = lx.entry_text(p)
                ov = lx.topical(dtoks, lx.tokens(ptext))
                if ov >= _CONTRA_OVERLAP and lx.is_assertive(lx.strip_quoted(ptext)):
                    if lx.has_negation(lx.strip_quoted(ptext)) != draft_neg:
                        warnings.append(self.warn(
                            "red", "prior_contradiction",
                            "You appear to have argued the opposite before.",
                            current=lx.excerpt(draft), prior=lx.excerpt(ptext),
                            source=lx.entry_id(p), confidence=round(ov, 3)))
                        break

        # 2. repeated unresolved claim (yellow) — raised >= N times, no receipt
        repeats = [p for p in (ctx.prior_participations or [])
                   if lx.topical(dtoks, lx.tokens(lx.entry_text(p))) >= _REPEAT_OVERLAP]
        if len(repeats) >= _REPEAT_MIN:
            resolved = any(lx.topical(dtoks, rt) >= _CONTRA_OVERLAP for _, rt, _ in receipts_tok)
            if not resolved:
                warnings.append(self.warn(
                    "yellow", "repeated_unresolved_claim",
                    f"You have raised this claim {len(repeats)} previous times "
                    f"without a confirming receipt.",
                    current=lx.excerpt(draft),
                    source=(lx.entry_id(repeats[-1])), confidence=0.6,
                    occurrences=len(repeats)))

        # 3. prior correction (yellow) — a receipt resolving this claim as false
        for rid, rt, r in receipts_tok:
            if lx.topical(dtoks, rt) >= _CONTRA_OVERLAP and \
                    str(r.get("resolution", "")).lower() in ("false", "corrected", "rejected"):
                warnings.append(self.warn(
                    "yellow", "prior_correction",
                    "A previous version of this claim was resolved as "
                    f"{r.get('resolution')}.",
                    current=lx.excerpt(draft), prior=lx.excerpt(r.get("text", "")),
                    source=rid, confidence=0.7))
                break

        # 4. attribution mismatch (red) — ONLY with an explicit supplied map.
        if ctx.attribution_map:
            named = self._named(draft)
            low = draft.lower()
            for phrase, correct_seat in ctx.attribution_map.items():
                if phrase and phrase.lower() in low and correct_seat \
                        and correct_seat not in named and named:
                    warnings.append(self.warn(
                        "red", "attribution_mismatch",
                        f'This references "{phrase}", which the supplied ledger '
                        f"attributes to {correct_seat}.",
                        current=lx.excerpt(draft), source=correct_seat,
                        confidence=0.8, phrase=phrase, named_in_draft=named[:5]))
                    break
        return warnings

    @staticmethod
    def _named(text):
        import re
        return re.findall(r"\b([A-Z][a-zA-Z0-9]{2,})\b", text or "")
