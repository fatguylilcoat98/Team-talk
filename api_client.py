"""Async API wrappers for every AI on the Team Talk roster.

Two provider types cover almost everything:
  - "anthropic"  → the Anthropic API (Claude)
  - "openai"     → the OpenAI API, or ANY OpenAI-compatible endpoint via
                   base_url: Grok (https://api.x.ai/v1), Gemini
                   (https://generativelanguage.googleapis.com/v1beta/openai/),
                   DeepSeek, local Ollama (http://localhost:11434/v1), ...

Each call catches its own errors and returns a result dict, so one AI
failing never blocks the others.
"""

import os
import re
from typing import Optional

import anthropic
import openai
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

import settings_store

MAX_TOKENS = int(os.getenv("MAX_TOKENS_CLAUDE", os.getenv("MAX_TOKENS", "2000")))
# Retry budget when a reasoning model burns all of MAX_TOKENS thinking and
# emits no visible text (Muse Spark, DeepSeek R1, o-series, ...).
REASONING_MAX_TOKENS = int(os.getenv("MAX_TOKENS_REASONING", "8000"))
API_TIMEOUT = float(os.getenv("API_TIMEOUT", "30"))

OPENAI_NO_CREDITS_MSG = (
    "Error: this AI's account is out of credits — the provider reports it as "
    "a rate limit. Add credits on the provider's billing page, then try again."
)

# Clients cached per (provider, base_url, key) so a key change in Settings
# takes effect on the next message without restarting the server.
_clients = {}

# Models that proved to be reasoning models (burned a whole normal budget on
# thinking and returned nothing). They start at the big budget from then on,
# skipping the doomed first call that just wastes tokens.
_reasoning_models = set()


def anthropic_key() -> Optional[str]:
    return settings_store.resolve("anthropic_api_key", "ANTHROPIC_API_KEY")


def openai_key() -> Optional[str]:
    return settings_store.resolve("openai_api_key", "OPENAI_API_KEY")


def participant_key(p: dict) -> Optional[str]:
    """The participant's own key, falling back to the matching global key."""
    if p.get("api_key"):
        return p["api_key"]
    if p.get("provider") == "anthropic":
        return anthropic_key()
    if p.get("base_url"):
        return None  # non-OpenAI endpoints need their own key
    return openai_key()


def _get_client(p: dict, key: str):
    cache_key = (p.get("provider"), p.get("base_url") or "", key)
    client = _clients.get(cache_key)
    if client is None:
        if p.get("provider") == "anthropic":
            client = AsyncAnthropic(api_key=key, timeout=API_TIMEOUT)
        else:
            client = AsyncOpenAI(api_key=key, base_url=p.get("base_url") or None, timeout=API_TIMEOUT)
        _clients[cache_key] = client
    return client


async def _list_model_ids(client, p: dict) -> list:
    """The model IDs the provider serves right now (Gemini prefixes 'models/')."""
    if p.get("provider") == "anthropic":
        page = await client.models.list(limit=100)
    else:
        page = await client.models.list()
    ids = []
    for m in getattr(page, "data", []) or []:
        mid = getattr(m, "id", "") or ""
        if mid.startswith("models/"):
            mid = mid[len("models/"):]
        if mid:
            ids.append(mid)
    return ids


_NON_CHAT = ("embed", "tts", "audio", "image", "whisper", "dall-e", "imagen",
             "veo", "aqa", "moderation", "transcribe", "realtime")
_CHEAP = ("flash", "lite", "mini", "haiku", "nano")


def _rank_models(ids: list, configured: str) -> list:
    """Order the provider's models by similarity to the configured (dead)
    one, favoring cheap chat models and burying embeddings/TTS/etc."""
    tokens = [t for t in re.split(r"[-./]", (configured or "").lower()) if len(t) > 2]

    def score(mid: str) -> int:
        low = mid.lower()
        s = sum(1 for t in tokens if t in low)
        s += sum(1 for c in _CHEAP if c in low)
        s -= sum(5 for b in _NON_CHAT if b in low)
        return s

    unique = set(ids)
    chat = [m for m in unique if score(m) >= 0] or list(unique)
    return sorted(chat, key=lambda m: (-score(m), m))[:5]


async def _suggest_models(client, p: dict) -> str:
    """After a model 404: ask the provider what it DOES serve, so the error
    message hands Chris working model names instead of a scavenger hunt."""
    try:
        ids = await _list_model_ids(client, p)
    except Exception:
        return ""
    if not ids:
        return ""
    return " This provider currently serves: " + ", ".join(_rank_models(ids, p.get("model") or "")) + "."


def _rate_limit_message(name: str, e: Exception) -> str:
    # Providers return 429 both for real rate limits and for accounts with
    # a $0 balance; the latter usually carries "insufficient_quota".
    if "insufficient_quota" in str(e):
        return OPENAI_NO_CREDITS_MSG
    return f"Error: {name}'s API is rate limited — wait a moment and retry."


async def call_participant(p: dict, system: str, prompt: str, images: list = None,
                           max_tokens: int = None) -> dict:
    """Call one AI with its system prompt + built context.

    images: [{"media_type": "image/png", "data": "<base64>"}] — attached
    pictures from this round, passed natively so vision models can see them.
    max_tokens: caller override for long-form work (Workshop bench turns
    carry a whole artifact and were getting cut off at the chat budget).

    Returns {text, tokens, ok} — never raises.
    """
    call_budget = max_tokens or MAX_TOKENS
    name = p.get("name", "AI")
    key = participant_key(p)
    if not key:
        return {
            "text": f"Error: no API key configured for {name} — add one in Settings.",
            "tokens": 0,
            "ok": False,
        }
    try:
        client = _get_client(p, key)
        if p.get("provider") == "anthropic":
            if images:
                content = [
                    {"type": "image", "source": {"type": "base64",
                     "media_type": img["media_type"], "data": img["data"]}}
                    for img in images
                ]
                content.append({"type": "text", "text": prompt})
            else:
                content = prompt
            response = await client.messages.create(
                model=p["model"],
                max_tokens=call_budget,
                system=system,
                messages=[{"role": "user", "content": content}],
            )
            text = "".join(b.text for b in response.content if b.type == "text")
            tokens = response.usage.output_tokens
        else:
            if images:
                content = [
                    {"type": "image_url", "image_url":
                     {"url": f"data:{img['media_type']};base64,{img['data']}"}}
                    for img in images
                ]
                content.append({"type": "text", "text": prompt})
            else:
                content = prompt
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ]
            budget = max(call_budget,
                         REASONING_MAX_TOKENS if p["model"] in _reasoning_models else 0)
            response = await client.chat.completions.create(
                model=p["model"], max_tokens=budget, messages=messages,
            )
            choice = response.choices[0]
            text = choice.message.content or ""
            tokens = response.usage.completion_tokens if response.usage else 0
            if not text.strip() and getattr(choice, "finish_reason", "") == "length" \
                    and budget < REASONING_MAX_TOKENS:
                # Reasoning models (Muse Spark, DeepSeek R1, o-series) can burn
                # the whole budget on internal thinking and get cut off before
                # writing a single visible word. Retry once with real headroom —
                # and remember, so future calls skip the doomed first attempt.
                _reasoning_models.add(p["model"])
                response = await client.chat.completions.create(
                    model=p["model"], max_tokens=REASONING_MAX_TOKENS, messages=messages,
                )
                choice = response.choices[0]
                text = choice.message.content or ""
                tokens += response.usage.completion_tokens if response.usage else 0
        if not text.strip():
            return {
                "text": (
                    f"Error: {name} returned an empty reply — its model likely spent the "
                    f"whole token budget on internal reasoning. Try again, or switch "
                    f"{name} to a non-reasoning model in Settings → {name} → Advanced."
                ),
                "tokens": tokens, "ok": False,
            }
        return {"text": text, "tokens": tokens, "ok": True}
    except (anthropic.RateLimitError, openai.RateLimitError) as e:
        return {"text": _rate_limit_message(name, e), "tokens": 0, "ok": False}
    except (anthropic.AuthenticationError, openai.AuthenticationError):
        return {"text": f"Error: {name}'s API key was rejected (invalid or revoked).", "tokens": 0, "ok": False}
    except (anthropic.BadRequestError, openai.BadRequestError) as e:
        msg = str(getattr(e, "message", e))
        if images and ("image" in msg.lower() or "vision" in msg.lower()):
            return {
                "text": (
                    f"Error: {name}'s model \"{p.get('model')}\" can't see images. The "
                    f"other AIs still saw the picture — to fix {name}, open Settings → "
                    f"{name} → Advanced and switch to a vision model (e.g. gpt-4o-mini "
                    f"for OpenAI)."
                ),
                "tokens": 0, "ok": False,
            }
        return {"text": f"Error: {name}'s API rejected the request (400): {msg}", "tokens": 0, "ok": False}
    except (anthropic.NotFoundError, openai.NotFoundError):
        # Providers retire models — the most common 404 by far. Ask the
        # provider what it serves NOW so the fix is copy-paste.
        suggestions = await _suggest_models(client, p)
        return {
            "text": (
                f"Error: {name}'s model \"{p.get('model')}\" was not found — the provider "
                f"retired it.{suggestions} Open Settings → {name} → Advanced, paste one of "
                f"those into Model, and Save."
            ),
            "tokens": 0, "ok": False,
        }
    except (anthropic.APIStatusError, openai.APIStatusError) as e:
        return {"text": f"Error: {name}'s API returned an error ({e.status_code}): {getattr(e, 'message', e)}", "tokens": 0, "ok": False}
    except (anthropic.APIConnectionError, openai.APIConnectionError):
        return {"text": f"Error: could not reach {name}'s API — check the server's network / base URL.", "tokens": 0, "ok": False}
    except Exception as e:
        return {"text": f"Error: {e}", "tokens": 0, "ok": False}


async def test_participant(p: dict) -> dict:
    """Validate one AI's key/endpoint with a zero-token models.list() call."""
    name = p.get("name", "AI")
    key = participant_key(p)
    if not key:
        return {"id": p.get("id"), "name": name, "ok": False, "detail": "No API key configured."}
    try:
        client = _get_client(p, key)
        ids = await _list_model_ids(client, p)
        model = p.get("model") or ""
        if ids and model and model not in ids:
            return {
                "id": p.get("id"), "name": name, "ok": False,
                "detail": (
                    f'Key works, but model "{model}" is not in the provider\'s current '
                    f"list — it may be retired. Try one of: "
                    + ", ".join(_rank_models(ids, model)) + "."
                ),
            }
        return {"id": p.get("id"), "name": name, "ok": True, "detail": "Key is valid."}
    except (anthropic.AuthenticationError, openai.AuthenticationError):
        return {"id": p.get("id"), "name": name, "ok": False, "detail": "Key was rejected (invalid or revoked)."}
    except (anthropic.RateLimitError, openai.RateLimitError) as e:
        detail = "Account is out of credits — add billing on the provider's site." \
            if "insufficient_quota" in str(e) else "Rate limited — try again in a moment."
        return {"id": p.get("id"), "name": name, "ok": False, "detail": detail}
    except (anthropic.APIStatusError, openai.APIStatusError) as e:
        return {"id": p.get("id"), "name": name, "ok": False, "detail": f"API error ({e.status_code})."}
    except (anthropic.APIConnectionError, openai.APIConnectionError):
        return {"id": p.get("id"), "name": name, "ok": False, "detail": "Could not reach the API — check network / base URL."}
    except Exception as e:
        return {"id": p.get("id"), "name": name, "ok": False, "detail": f"Error: {e}"}
