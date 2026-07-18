"""Feature flags for the reflection layer.

Defaults: the layer is OFF; when turned on it starts in SHADOW mode (evaluate
and store, but change nothing the model or user sees). An explicit settings
value overrides the environment variable.

  REFLECTION_LAYER_ENABLED      default: false
  REFLECTION_LAYER_SHADOW_MODE  default: true   (evaluate + store, change nothing)
  REFLECTION_LAYER_VISIBLE      default: false  (attach a reflection card to the
                                                 response so the room can see it)
"""

import os


def _truthy(v) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def enabled(settings: dict = None) -> bool:
    if settings and "reflection_layer_enabled" in settings:
        return bool(settings.get("reflection_layer_enabled"))
    return _truthy(os.getenv("REFLECTION_LAYER_ENABLED", ""))


def shadow_mode(settings: dict = None) -> bool:
    if settings and "reflection_layer_shadow_mode" in settings:
        return bool(settings.get("reflection_layer_shadow_mode"))
    v = os.getenv("REFLECTION_LAYER_SHADOW_MODE")
    return _truthy(v) if v is not None else True


def visible(settings: dict = None) -> bool:
    """Visible mode: attach a reflection card to each response so the room sees
    it. Only meaningful when enabled; it does not change the response TEXT."""
    if settings and "reflection_layer_visible" in settings:
        return bool(settings.get("reflection_layer_visible"))
    return _truthy(os.getenv("REFLECTION_LAYER_VISIBLE", ""))
