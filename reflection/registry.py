"""Explicit reflection-pass registry.

No dynamic loading from arbitrary files (that would be a security and
determinism hazard). Passes are registered explicitly; adding a future pass is
a one-line change plus its module. The engine runs passes in registration
order, which is deterministic.
"""

from .passes.mirror import MirrorPass
from .passes.assumption import AssumptionPass
from .passes.confidence import ConfidencePass

# Default deterministic order. Future passes (Memory Verification, Missing
# Information, Perspective, Question, Silence, Outcome, Regret, Pattern) are
# added here when built — no engine redesign required.
DEFAULT_PASSES = (
    MirrorPass(),
    AssumptionPass(),
    ConfidencePass(),
)


def default_passes():
    return tuple(DEFAULT_PASSES)


def with_passes(*passes):
    """Build a custom ordered pass tuple (used by tests and future callers)."""
    return tuple(passes)
