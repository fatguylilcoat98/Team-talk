"""Server-side settings storage for Team Talk.

Settings live in config/settings.json (gitignored, chmod 600) so Chris can
manage API keys from the web UI instead of editing .env over SSH.

Resolution order everywhere in the app:
    1. config/settings.json (saved from the Settings page)
    2. environment variables (which includes anything loaded from .env)
    3. built-in default
"""

import json
import os
import re
from typing import Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
SETTINGS_PATH = os.path.join(CONFIG_DIR, "settings.json")

# Only these fields are ever read from / written to the settings file
ALLOWED_FIELDS = {
    "anthropic_api_key",
    "openai_api_key",
    "claude_model",
    "chatgpt_model",
    "host",
    "port",
}


def load() -> dict:
    """Read the settings file. Returns {} if missing or unreadable."""
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if k in ALLOWED_FIELDS}


def get(name: str) -> Optional[str]:
    value = load().get(name)
    if value in (None, ""):
        return None
    return value


def save(updates: dict) -> dict:
    """Merge updates into the settings file. Returns the merged settings.

    The file is written atomically with owner-only permissions (0600),
    and the config dir is created on demand (0700).
    """
    settings = load()
    for key, value in updates.items():
        if key not in ALLOWED_FIELDS:
            continue
        if value in (None, ""):
            continue  # blank means "leave unchanged" — clearing is reset()
        settings[key] = value

    os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)
    tmp = f"{SETTINGS_PATH}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    os.replace(tmp, SETTINGS_PATH)
    os.chmod(SETTINGS_PATH, 0o600)
    return settings


def reset() -> bool:
    """Delete the settings file entirely — the app falls back to env/.env."""
    try:
        os.remove(SETTINGS_PATH)
        return True
    except FileNotFoundError:
        return False


def resolve(name: str, env_name: str, default: Optional[str] = None) -> Optional[str]:
    """Saved settings first, then environment (.env is loaded into env)."""
    return get(name) or os.getenv(env_name) or default


def source(name: str, env_name: str) -> Optional[str]:
    """Where the current value comes from: 'settings', 'environment', or None."""
    if get(name):
        return "settings"
    if os.getenv(env_name):
        return "environment"
    return None


def mask_key(key: Optional[str]) -> str:
    """Masked display form — never return a full key to the browser.

    Examples: sk-ant-api03-•••••• / sk-proj-•••••• / sk-••••••
    """
    if not key:
        return ""
    m = re.match(r"^(sk-ant-[A-Za-z0-9]{2,8}-|sk-proj-|sk-)", key)
    prefix = m.group(1) if m else key[:4]
    return f"{prefix}••••••"
