"""The reflection engine: run registered passes in deterministic order,
isolate failures, aggregate severity, and build one ReflectionResult.

Pure with respect to the reasoning ledger, prompts, and sessions — it reads
none of them. The only impurity is a timestamp/uuid on the result and a
duration measurement; nothing here performs I/O, model calls, or network.
"""

import time
import uuid
from datetime import datetime, timezone

from . import lexicon as lx
from .models import (ENGINE_VERSION, SCHEMA_VERSION, PassResult, ReflectionResult,
                     worst)
from .registry import default_passes


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def reflect(context, passes=None, shadow_mode=True) -> ReflectionResult:
    """Evaluate one draft. Never raises: a pass that throws is recorded as a
    failed PassResult and the others still run."""
    passes = passes if passes is not None else default_passes()
    started = time.perf_counter()
    pass_results, all_warnings = [], []

    for p in passes:
        t = time.perf_counter()
        ok, err, ws = True, None, []
        try:
            ws = list(p.evaluate(context) or [])
        except Exception as e:               # one pass failing must not stop others
            ok, err, ws = False, str(e)[:200], []
        dur = (time.perf_counter() - t) * 1000
        pass_results.append(PassResult(
            pass_name=getattr(p, "name", "?"),
            severity=worst([w.severity for w in ws]),
            warnings=ws, duration_ms=round(dur, 4), ok=ok, error=err))
        all_warnings.extend(ws)

    matched = sorted({w.source_reference for w in all_warnings if w.source_reference})
    conf_delta = (lx.certainty_score(context.draft_text)
                  - max([lx.certainty_score(lx.entry_text(p))
                         for p in (context.prior_participations or [])], default=0))

    return ReflectionResult(
        schema_version=SCHEMA_VERSION,
        reflection_id=f"reflection_{uuid.uuid4().hex[:12]}",
        author=context.author,
        timestamp=_now(),
        participation_id=context.participation_id,
        overall_severity=worst([w.severity for w in all_warnings]),
        pass_results=pass_results,
        warnings=all_warnings,
        matched_claims=matched,
        confidence_delta=conf_delta,
        revision_performed=False,
        shadow_mode=bool(shadow_mode),
        engine_version=ENGINE_VERSION,
        duration_ms=round((time.perf_counter() - started) * 1000, 4),
    )
