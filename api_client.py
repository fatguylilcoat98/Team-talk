"""Async wrappers around the Anthropic and OpenAI clients.

Both calls are designed to be awaited together via asyncio.gather() —
each catches its own errors and returns a result dict, so one API
failing never blocks the other's response.

Configuration (keys, models) is resolved per call through
settings_store: saved settings first, then environment / .env. Clients
are rebuilt automatically when the key changes via the Settings page.
"""

import os
from typing import Optional

import anthropic
import openai
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

import settings_store
from conversation import CLAUDE_SYSTEM_PROMPT, CHATGPT_SYSTEM_PROMPT

DEFAULT_CLAUDE_MODEL = "claude-opus-4-8"
DEFAULT_CHATGPT_MODEL = "gpt-4-turbo"

MAX_TOKENS_CLAUDE = int(os.getenv("MAX_TOKENS_CLAUDE", "2000"))
MAX_TOKENS_CHATGPT = int(os.getenv("MAX_TOKENS_CHATGPT", "2000"))
API_TIMEOUT = float(os.getenv("API_TIMEOUT", "30"))

# Clients are cached per key so a key change in Settings takes effect
# on the next message without restarting the server.
_claude_client: Optional[AsyncAnthropic] = None
_claude_client_key: Optional[str] = None
_openai_client: Optional[AsyncOpenAI] = None
_openai_client_key: Optional[str] = None


def anthropic_key() -> Optional[str]:
    return settings_store.resolve("anthropic_api_key", "ANTHROPIC_API_KEY")


def openai_key() -> Optional[str]:
    return settings_store.resolve("openai_api_key", "OPENAI_API_KEY")


def claude_model() -> str:
    return settings_store.resolve("claude_model", "CLAUDE_MODEL", DEFAULT_CLAUDE_MODEL)


def chatgpt_model() -> str:
    return settings_store.resolve("chatgpt_model", "CHATGPT_MODEL", DEFAULT_CHATGPT_MODEL)


def _get_claude() -> AsyncAnthropic:
    global _claude_client, _claude_client_key
    key = anthropic_key()
    if not key:
        raise RuntimeError(
            "Anthropic API key is not set — add it in Settings (gear button) or in .env"
        )
    if _claude_client is None or _claude_client_key != key:
        _claude_client = AsyncAnthropic(api_key=key, timeout=API_TIMEOUT)
        _claude_client_key = key
    return _claude_client


def _get_openai() -> AsyncOpenAI:
    global _openai_client, _openai_client_key
    key = openai_key()
    if not key:
        raise RuntimeError(
            "OpenAI API key is not set — add it in Settings (gear button) or in .env"
        )
    if _openai_client is None or _openai_client_key != key:
        _openai_client = AsyncOpenAI(api_key=key, timeout=API_TIMEOUT)
        _openai_client_key = key
    return _openai_client


async def call_claude(prompt: str) -> dict:
    """Call Claude with the built context. Returns {text, tokens, ok}."""
    try:
        client = _get_claude()
        response = await client.messages.create(
            model=claude_model(),
            max_tokens=MAX_TOKENS_CLAUDE,
            system=CLAUDE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in response.content if b.type == "text")
        return {"text": text, "tokens": response.usage.output_tokens, "ok": True}
    except anthropic.RateLimitError:
        return {"text": "Error: Claude API rate limited — wait a moment and retry.", "tokens": 0, "ok": False}
    except anthropic.APIStatusError as e:
        return {"text": f"Error: Claude API error ({e.status_code}): {e.message}", "tokens": 0, "ok": False}
    except anthropic.APIConnectionError:
        return {"text": "Error: could not reach the Claude API — check your network.", "tokens": 0, "ok": False}
    except Exception as e:
        return {"text": f"Error: {e}", "tokens": 0, "ok": False}


async def call_chatgpt(prompt: str) -> dict:
    """Call ChatGPT with the built context. Returns {text, tokens, ok}."""
    try:
        client = _get_openai()
        response = await client.chat.completions.create(
            model=chatgpt_model(),
            max_tokens=MAX_TOKENS_CHATGPT,
            messages=[
                {"role": "system", "content": CHATGPT_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )
        text = response.choices[0].message.content or ""
        tokens = response.usage.completion_tokens if response.usage else 0
        return {"text": text, "tokens": tokens, "ok": True}
    except openai.RateLimitError:
        return {"text": "Error: OpenAI API rate limited — wait a moment and retry.", "tokens": 0, "ok": False}
    except openai.APIStatusError as e:
        return {"text": f"Error: OpenAI API error ({e.status_code}): {e.message}", "tokens": 0, "ok": False}
    except openai.APIConnectionError:
        return {"text": "Error: could not reach the OpenAI API — check your network.", "tokens": 0, "ok": False}
    except Exception as e:
        return {"text": f"Error: {e}", "tokens": 0, "ok": False}


# --- Key testing (used by the Settings page) ------------------------------
#
# models.list() validates the key without spending any tokens.

async def test_anthropic_key(key: Optional[str] = None) -> dict:
    key = key or anthropic_key()
    if not key:
        return {"ok": False, "detail": "No Anthropic key configured."}
    try:
        client = AsyncAnthropic(api_key=key, timeout=API_TIMEOUT)
        await client.models.list()
        return {"ok": True, "detail": "Anthropic key is valid."}
    except anthropic.AuthenticationError:
        return {"ok": False, "detail": "Anthropic key was rejected (invalid or revoked)."}
    except anthropic.APIStatusError as e:
        return {"ok": False, "detail": f"Anthropic API error ({e.status_code}): {e.message}"}
    except anthropic.APIConnectionError:
        return {"ok": False, "detail": "Could not reach the Anthropic API — check the server's network."}
    except Exception as e:
        return {"ok": False, "detail": f"Error: {e}"}


async def test_openai_key(key: Optional[str] = None) -> dict:
    key = key or openai_key()
    if not key:
        return {"ok": False, "detail": "No OpenAI key configured."}
    try:
        client = AsyncOpenAI(api_key=key, timeout=API_TIMEOUT)
        await client.models.list()
        return {"ok": True, "detail": "OpenAI key is valid."}
    except openai.AuthenticationError:
        return {"ok": False, "detail": "OpenAI key was rejected (invalid or revoked)."}
    except openai.APIStatusError as e:
        return {"ok": False, "detail": f"OpenAI API error ({e.status_code}): {e.message}"}
    except openai.APIConnectionError:
        return {"ok": False, "detail": "Could not reach the OpenAI API — check the server's network."}
    except Exception as e:
        return {"ok": False, "detail": f"Error: {e}"}
