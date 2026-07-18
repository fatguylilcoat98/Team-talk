"""Data contracts for the reflection layer. Plain dataclasses with to_dict()
so results serialize cleanly to the append-only JSONL store.
"""

from dataclasses import dataclass, field, asdict
from typing import List, Optional

SCHEMA_VERSION = 1
ENGINE_VERSION = "reflection/1"

SEVERITY_RANK = {"green": 0, "yellow": 1, "red": 2}


def worst(severities) -> str:
    sev = [s for s in severities if s in SEVERITY_RANK]
    return max(sev, key=lambda s: SEVERITY_RANK[s]) if sev else "green"


@dataclass
class ReflectionContext:
    """Everything a pass may read. All optional fields degrade to empty; the
    engine never infers evidence that was not supplied."""
    author: str
    draft_text: str
    participation_id: Optional[str] = None
    prior_participations: List[dict] = field(default_factory=list)  # author's own: [{id,text}]
    prior_claims: List[dict] = field(default_factory=list)
    unresolved_claims: List[dict] = field(default_factory=list)
    receipts: List[dict] = field(default_factory=list)              # [{id,text,resolution?}]
    attribution_map: dict = field(default_factory=dict)             # {phrase: correct_seat}
    timestamp: Optional[str] = None
    metadata: dict = field(default_factory=dict)


@dataclass
class ReflectionWarning:
    warning_id: str
    pass_name: str
    severity: str          # green | yellow | red
    category: str          # stable machine-readable
    message: str
    current_excerpt: str = ""
    prior_excerpt: str = ""
    source_reference: Optional[str] = None
    detector_confidence: float = 0.0   # the HEURISTIC's match confidence, not epistemic
    metadata: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)


@dataclass
class PassResult:
    pass_name: str
    severity: str
    warnings: List[ReflectionWarning]
    duration_ms: float
    ok: bool = True
    error: Optional[str] = None

    def to_dict(self):
        d = asdict(self)
        d["warnings"] = [w.to_dict() for w in self.warnings]
        return d


@dataclass
class ReflectionResult:
    schema_version: int
    reflection_id: str
    author: str
    timestamp: Optional[str]
    participation_id: Optional[str]
    overall_severity: str
    pass_results: List[PassResult]
    warnings: List[ReflectionWarning]
    matched_claims: List[str]
    confidence_delta: int
    revision_performed: bool
    shadow_mode: bool
    engine_version: str
    duration_ms: float

    def to_dict(self):
        d = asdict(self)
        d["pass_results"] = [p.to_dict() for p in self.pass_results]
        d["warnings"] = [w.to_dict() for w in self.warnings]
        return d
