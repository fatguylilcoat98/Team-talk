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
from typing import Optional

import anthropic
import openai
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

import settings_store

MAX_TOKENS = int(os.getenv("MAX_TOKENS_CLAUDE", os.getenv("MAX_TOKENS", "2000")))
API_TIMEOUT = float(os.getenv("API_TIMEOUT", "30"))

OPENAI_NO_CREDITS_MSG = (
    "Error: this AI's account is out of credits — the provider reports it as "
    "a rate limit. Add credits on the provider's billing page, then try again."
)

# Clients cached per (provider, base_url, key) so a key change in Settings
# takes effect on the next message without restarting the server.
_clients = {}


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


def _rate_limit_message(name: str, e: Exception) -> str:
    # Providers return 429 both for real rate limits and for accounts with
    # a $0 balance; the latter usually carries "insufficient_quota".
    if "insufficient_quota" in str(e):
        return OPENAI_NO_CREDITS_MSG
    return f"Error: {name}'s API is rate limited — wait a moment and retry."


async def call_participant(p: dict, system: str, prompt: str, images: list = None) -> dict:
    """Call one AI with its system prompt + built context.

    images: [{"media_type": "image/png", "data": "<base64>"}] — attached
    pictures from this round, passed natively so vision models can see them.

    Returns {text, tokens, ok} — never raises.
    """
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
                max_tokens=MAX_TOKENS,
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
            response = await client.chat.completions.create(
                model=p["model"],
                max_tokens=MAX_TOKENS,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": content},
                ],
            )
            text = response.choices[0].message.content or ""
            tokens = response.usage.completion_tokens if response.usage else 0
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
    except (anthropic.NotFoundError, openai.NotFoundError) as e:
        # Providers retire models — the most common 404 by far
        return {
            "text": (
                f"Error: {name}'s model \"{p.get('model')}\" was not found — the provider "
                f"may have retired it. Open Settings → {name} → Advanced and update the "
                f"Model name. (Provider said: {getattr(e, 'message', e)})"
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
        await client.models.list()
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
