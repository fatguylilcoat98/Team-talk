"""Send-path integration hook — shadow + visible modes.

Called immediately after a chat round's responses are finalized and before the
round is persisted. Flag-gated and best-effort:

  * flag OFF   -> one env check, returns; response byte-identical, nothing stored.
  * shadow ON  -> evaluate each finalized response against that author's own
                  prior session messages + a bounded read-only ledger snapshot,
                  append a ReflectionResult to the separate store. Shows nothing;
                  the response TEXT and the session are unchanged.
  * visible ON -> as shadow, PLUS attach a compact, bounded reflection CARD to
                  the response object so the room can see the pass statuses and
                  warnings. This adds a metadata field; it never changes the
                  response TEXT, never rewrites the answer, and never blocks.

Any exception is swallowed — reflection must never prevent a reply from being
sent or stored, and never mutate the reasoning ledger.
"""

from . import engine, flags, snapshot, store
from .models import ReflectionContext

_MAX_CARD_WARNINGS = 6


def _author_prior(prior_rounds, author):
    out = []
    for r in prior_rounds or []:
        rn = r.get("round")
        for resp in r.get("responses", []):
            if resp.get("name") == author and (resp.get("text") or "").strip():
                out.append({"id": f"r{rn}:{author}", "text": resp["text"]})
    return out


def _card(result) -> dict:
    """A compact, bounded card for the UI. Only bounded excerpts (already
    capped inside warnings) — no full messages, no secrets, no chain-of-thought."""
    passes = {p.pass_name: p.severity for p in result.pass_results}
    return {
        "reflection_id": result.reflection_id,
        "overall_severity": result.overall_severity,
        "passes": {"mirror": passes.get("mirror", "green"),
                   "assumption": passes.get("assumption", "green"),
                   "confidence": passes.get("confidence", "green")},
        "warnings": [{
            "category": w.category, "severity": w.severity, "message": w.message,
            "current_excerpt": w.current_excerpt, "prior_excerpt": w.prior_excerpt,
            "source_reference": w.source_reference,
        } for w in result.warnings[:_MAX_CARD_WARNINGS]],
    }


def reflect_round(session, round_data, participants=None, settings=None) -> int:
    """Evaluate every content-bearing response in a just-finalized round.
    Returns the number of reflections stored (0 when disabled). Never raises."""
    try:
        if not flags.enabled(settings):
            return 0
        shadow = flags.shadow_mode(settings)
        show = flags.visible(settings)
        prior_rounds = session.get("rounds", [])
        snap = snapshot.ledger_snapshot()          # bounded, read-only, safe on failure
        stored = 0
        for resp in round_data.get("responses", []):
            author = resp.get("name")
            text = resp.get("text") or ""
            if not author or not text.strip():
                continue
            ctx = ReflectionContext(
                author=author,
                draft_text=text,
                participation_id=None,
                prior_participations=_author_prior(prior_rounds, author),
                receipts=snap["receipts"],
                attribution_map=snap["attribution_map"],
                metadata={"session_id": session.get("id"), "round": round_data.get("round")},
            )
            result = engine.reflect(ctx, shadow_mode=shadow and not show)
            if store.record(result) is not None:
                stored += 1
            # VISIBLE: attach the card (does NOT touch resp["text"]).
            if show:
                resp["reflection"] = _card(result)
        return stored
    except Exception as e:                          # pragma: no cover
        print(f"[reflection] hook failed (ignored, response unaffected): {e}")
        return 0
