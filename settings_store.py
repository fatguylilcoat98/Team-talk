"""Server-side settings storage for Team Talk.

Settings live in config/settings.json (gitignored, chmod 600) so Chris can
manage API keys and the AI roster from the web UI instead of editing .env.

Resolution order everywhere in the app:
    1. config/settings.json (saved from the Settings page)
    2. environment variables (which includes anything loaded from .env)
    3. built-in default
"""

import json
import os
import re
import uuid
from typing import List, Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
SETTINGS_PATH = os.path.join(CONFIG_DIR, "settings.json")

# Only these fields are ever read from / written to the settings file
ALLOWED_FIELDS = {
    "anthropic_api_key",
    "openai_api_key",
    "host",
    "port",
    "participants",
}

# One color per AI, assigned in roster order (Chris is gold, reserved)
PALETTE = [
    "#d97757",  # Claude orange
    "#4bb388",  # ChatGPT green
    "#5b8def",  # blue
    "#b06fd8",  # purple
    "#d86f9c",  # pink
    "#6fc7d8",  # teal
]

MAX_PARTICIPANTS = 6

PARTICIPANT_FIELDS = {"id", "name", "provider", "model", "api_key", "base_url", "color", "persona"}
PROVIDERS = {"anthropic", "openai"}


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


def get(name: str):
    value = load().get(name)
    if value in (None, "", []):
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
        if key == "participants":
            if isinstance(value, list):
                settings[key] = value
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
    m = re.match(r"^(sk-ant-[A-Za-z0-9]{2,8}-|sk-proj-|sk-|xai-|AIza)", key)
    prefix = m.group(1) if m else key[:4]
    return f"{prefix}••••••"


# --- AI roster -------------------------------------------------------------

def default_participants() -> List[dict]:
    """The out-of-the-box two-AI roster, using the cheapest models."""
    return [
        {
            "id": "claude",
            "name": "Claude",
            "provider": "anthropic",
            "model": os.getenv("CLAUDE_MODEL", "claude-haiku-4-5"),
            "color": PALETTE[0],
        },
        {
            "id": "chatgpt",
            "name": "ChatGPT",
            "provider": "openai",
            "model": os.getenv("CHATGPT_MODEL", "gpt-4o-mini"),
            "color": PALETTE[1],
        },
    ]


def get_participants() -> List[dict]:
    """The active AI roster — saved roster, or the default pair."""
    saved = get("participants")
    if not saved:
        return default_participants()
    roster = []
    for i, p in enumerate(saved):
        if not isinstance(p, dict) or not p.get("name") or not p.get("model"):
            continue
        clean = {k: v for k, v in p.items() if k in PARTICIPANT_FIELDS and v not in (None, "")}
        if clean.get("provider") not in PROVIDERS:
            clean["provider"] = "openai"
        clean.setdefault("id", _slug(clean["name"]))
        clean.setdefault("color", PALETTE[i % len(PALETTE)])
        roster.append(clean)
    return roster or default_participants()


def sanitize_participants(incoming: List[dict]) -> List[dict]:
    """Validate a roster from the Settings UI and carry over unchanged keys.

    A blank api_key on an incoming participant means "keep the key already
    saved for this id" — the UI never sees full keys, so it can't echo them.
    """
    existing_by_id = {p.get("id"): p for p in (get("participants") or [])}
    roster = []
    seen_ids = set()
    for i, p in enumerate(incoming[:MAX_PARTICIPANTS]):
        if not isinstance(p, dict):
            continue
        name = str(p.get("name") or "").strip()
        model = str(p.get("model") or "").strip()
        if not name or not model:
            continue
        provider = p.get("provider") if p.get("provider") in PROVIDERS else "openai"
        pid = str(p.get("id") or "").strip() or _slug(name)
        while pid in seen_ids:
            pid = f"{pid}-{uuid.uuid4().hex[:4]}"
        seen_ids.add(pid)

        clean = {"id": pid, "name": name, "provider": provider, "model": model}
        persona = str(p.get("persona") or "").strip()[:300]
        if persona:
            clean["persona"] = persona
        base_url = str(p.get("base_url") or "").strip()
        if base_url:
            clean["base_url"] = base_url
        api_key = str(p.get("api_key") or "").strip()
        if api_key:
            clean["api_key"] = api_key
        elif existing_by_id.get(pid, {}).get("api_key"):
            clean["api_key"] = existing_by_id[pid]["api_key"]
        clean["color"] = p.get("color") or existing_by_id.get(pid, {}).get("color") or PALETTE[i % len(PALETTE)]
        roster.append(clean)
    return roster


def _slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or f"ai-{uuid.uuid4().hex[:6]}"
