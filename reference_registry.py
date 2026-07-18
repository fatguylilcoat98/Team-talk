"""Reference Type Registry — the mechanical contract for typed references.

This is where the Layer 0 / Layer 1 boundary is drawn. The registry
declares, for each reference type, exactly which deterministic checks the
validator (Layer 1, reasoning_observations.py) is permitted to run. The
validator has NO jurisdiction beyond this list. It may never infer intent,
dishonesty, concealment, or semantic equivalence — those Layer 2/3
questions are written down here as the `interpretation_boundary` so the
line is visible in the data, not merely in prose.

Versioned from the first entry: every derived observation carries the
`registry_version` that produced it, so a check's meaning is always
traceable to a specific contract.
"""

REGISTRY_VERSION = 1

_REGISTRY = {
    "retry_of": {
        "registry_version": REGISTRY_VERSION,
        "reference_type": "retry_of",
        "source_type": "participation",
        "target_type": "participation",
        "minimum_cardinality": 0,
        "maximum_cardinality": 1,
        "mechanical_checks": [
            "target_exists",
            "target_is_participation",
            "target_is_not_self",
        ],
        "interpretation_boundary": [
            "whether a missing retry_of edge was accidental",
            "whether a missing retry_of edge was intentional",
            "whether similar content represents a retry",
            "whether similar content represents a new claim",
        ],
        "detectability_invariant":
            "Every malformed or dangling retry_of reference is mechanically discoverable.",
    },
}


def version() -> int:
    """The current registry version. Stamped onto every observation."""
    return REGISTRY_VERSION


def get(reference_type: str):
    """The full contract for a reference type, or None if unregistered."""
    entry = _REGISTRY.get(reference_type)
    return dict(entry) if entry else None


def is_registered(reference_type: str) -> bool:
    return reference_type in _REGISTRY


def mechanical_checks(reference_type: str) -> list:
    """The finite, explicitly-declared checks Layer 1 may run for a type.

    This list IS the validator's jurisdiction. Returning a copy keeps the
    registry immutable from a caller's perspective.
    """
    entry = _REGISTRY.get(reference_type)
    return list(entry["mechanical_checks"]) if entry else []


def reference_types() -> list:
    return list(_REGISTRY.keys())
