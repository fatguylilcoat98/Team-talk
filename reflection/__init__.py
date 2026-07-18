"""FLINT Cognitive Reflection Layer v1.

A modular, deterministic reflection framework that runs immediately before an
AI response is finalized. It gives a model structured information about its own
prior statements, unresolved claims, available receipts, hidden assumptions,
attribution history, and whether its wording exceeds its evidence. It does not
tell the model what to believe: cognitive assistance, not censorship, and not a
replacement for reasoning.

No model calls, no network, no reading of hidden chain-of-thought — it
evaluates the final draft and structured historical records only.

Public surface:
    reflect(context)          -> ReflectionResult          (engine)
    ReflectionContext, ...    -> data contracts            (models)
    DEFAULT_PASSES            -> the three built-in passes  (registry)
    flags.enabled / shadow_mode
    hook.reflect_round        -> flag-gated send-path shadow hook
    store.record / list_reflections / analytics
"""

from . import engine, flags, hook, lexicon, registry, snapshot, store   # noqa: F401
from .engine import reflect                                    # noqa: F401
from .models import (ReflectionContext, ReflectionResult,       # noqa: F401
                     ReflectionWarning, PassResult)
from .registry import DEFAULT_PASSES                            # noqa: F401
