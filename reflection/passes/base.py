"""The stable reflection-pass interface.

A pass receives a ReflectionContext and returns a list of ReflectionWarning.
It must never modify the draft, write to storage, call a model, or make a
network request. Passes fail independently (the engine wraps each one) and are
measured independently. Keep them pure and deterministic.
"""

import uuid

from ..models import ReflectionWarning


class ReflectionPass:
    name = "base"

    def evaluate(self, context) -> list:
        raise NotImplementedError

    # small shared helper so passes build warnings consistently
    def warn(self, severity, category, message, *, current="", prior="",
             source=None, confidence=0.0, **metadata) -> ReflectionWarning:
        return ReflectionWarning(
            warning_id=f"warn_{uuid.uuid4().hex[:10]}",
            pass_name=self.name,
            severity=severity,
            category=category,
            message=message,
            current_excerpt=current,
            prior_excerpt=prior,
            source_reference=source,
            detector_confidence=round(float(confidence), 3),
            metadata=metadata,
        )
