"""Layer 1 — mechanical observations derived from the reasoning graph.

Pure functions of graph state. NOTHING here is persisted: observations are
computed on demand and returned, never written to any ledger or store.
Recompute after the graph changes and the answers change with it — a
dangling reference stops dangling the moment its target arrives.

Jurisdiction is the Reference Type Registry and nothing else. Every check
run here is one the registry explicitly declares; every observation carries
the `registry_version` that produced it, and attributes the finding to the
Participation that emitted the reference. This module cannot and must not
infer intent, concealment, dishonesty, or semantic equivalence — those are
Layer 2/3 questions, out of scope by construction.

Observation types produced (all deterministic, all mechanical):
  - dangling_reference          target_exists failed (target_not_found)
  - invalid_reference           target_is_participation / target_is_not_self
                                / cardinality failed (mechanical_fact says which)
  - missing_expected_reference  a structurally-declared resend carries no
                                retry_of edge to its declared original
"""

import reference_registry as registry
from reasoning_store import UNRESOLVED_TARGET

RETRY_OF = "retry_of"

# The complete, finite set of mechanical facts this layer can emit. Kept
# explicit so "the validator only speaks deterministic, declared facts" is
# checkable, not just asserted.
MECHANICAL_FACTS = {
    "target_not_found",
    "wrong_target_type",
    "target_is_self",
    "cardinality_exceeded",
}


def _index(participations):
    return {p["participation_id"]: p for p in participations
            if not p.get("_corrupt") and "participation_id" in p}


def _retry_edges(p):
    return [r for r in p.get("references", [])
            if isinstance(r, dict) and r.get("type") == RETRY_OF]


def observe(participations):
    """Return the mechanical observations for the given graph state.

    Deterministic: same input -> identical output, in participation order
    then reference order.
    """
    by_id = _index(participations)
    out = []
    for p in participations:
        if p.get("_corrupt") or "participation_id" not in p:
            continue
        out.extend(_observe_participation(p, by_id))
    return out


def _observe_participation(p, by_id):
    out = []
    ver = registry.version()
    checks = registry.mechanical_checks(RETRY_OF)      # the jurisdiction
    entry = registry.get(RETRY_OF)
    src = p["participation_id"]
    edges = _retry_edges(p)

    # cardinality (registry maximum_cardinality)
    if entry and len(edges) > entry["maximum_cardinality"]:
        out.append({
            "observation_type": "invalid_reference",
            "reference_type": RETRY_OF, "registry_version": ver,
            "source_participation_id": src,
            "mechanical_fact": "cardinality_exceeded",
            "count": len(edges),
        })

    for edge in edges:
        target_id = edge.get("target_id")
        # target_is_not_self
        if "target_is_not_self" in checks and target_id == src:
            out.append({
                "observation_type": "invalid_reference",
                "reference_type": RETRY_OF, "registry_version": ver,
                "source_participation_id": src,
                "target_participation_id": target_id,
                "mechanical_fact": "target_is_self",
            })
            continue
        # target_exists
        if "target_exists" in checks and target_id not in by_id:
            out.append({
                "observation_type": "dangling_reference",
                "reference_type": RETRY_OF, "registry_version": ver,
                "source_participation_id": src,
                "target_participation_id": target_id,
                "mechanical_fact": "target_not_found",
            })
            continue
        # target_is_participation (the declared edge target_type must be
        # 'participation'; the resolved object is one by construction here)
        if "target_is_participation" in checks and edge.get("target_type") != "participation":
            out.append({
                "observation_type": "invalid_reference",
                "reference_type": RETRY_OF, "registry_version": ver,
                "source_participation_id": src,
                "target_participation_id": target_id,
                "mechanical_fact": "wrong_target_type",
            })
            continue

    # A structurally-declared resend that carries no retry_of edge to its
    # declared original. This is the "retry omitted entirely" case. The
    # pairing signal is non-semantic (declared_resend_of), never inferred
    # from content — and it is intentionally NOT the same thing as the edge,
    # so a declaration without an edge is exactly what surfaces here.
    declared_resend_of = p.get("declared_resend_of")
    if declared_resend_of is not None:
        if declared_resend_of == UNRESOLVED_TARGET:
            satisfied = bool(edges)          # any retry_of edge resolves it
            expected = None                  # target could not be resolved
        else:
            satisfied = any(e.get("target_id") == declared_resend_of for e in edges)
            expected = declared_resend_of
        if not satisfied:
            out.append({
                "observation_type": "missing_expected_reference",
                "reference_type": RETRY_OF, "registry_version": ver,
                "source_participation_id": src,
                "expected_target_participation_id": expected,
            })
    return out


def observe_live():
    """Convenience: observe the current persisted graph from the store."""
    import reasoning_store
    return observe(reasoning_store.list_participations())
