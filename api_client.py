"""Async wrappers around the Anthropic and OpenAI clients.

Both calls are designed to be awaited together via asyncio.gather() —
each catches its own errors and returns a result dict, so one API
failing never blocks the other's response.
"""

import os

import anthropic
import openai
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from conversation import CLAUDE_SYSTEM_PROMPT, CHATGPT_SYSTEM_PROMPT

CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-4-8")
CHATGPT_MODEL = os.getenv("CHATGPT_MODEL", "gpt-4-turbo")
MAX_TOKENS_CLAUDE = int(os.getenv("MAX_TOKENS_CLAUDE", "2000"))
MAX_TOKENS_CHATGPT = int(os.getenv("MAX_TOKENS_CHATGPT", "2000"))
API_TIMEOUT = float(os.getenv("API_TIMEOUT", "30"))

_claude_client = None
_openai_client = None


def _get_claude() -> AsyncAnthropic:
    global _claude_client
    if _claude_client is None:
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise RuntimeError("ANTHROPIC_API_KEY is not set — add it to your .env file")
        _claude_client = AsyncAnthropic(timeout=API_TIMEOUT)
    return _claude_client


def _get_openai() -> AsyncOpenAI:
    global _openai_client
    if _openai_client is None:
        if not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY is not set — add it to your .env file")
        _openai_client = AsyncOpenAI(timeout=API_TIMEOUT)
    return _openai_client


async def call_claude(prompt: str) -> dict:
    """Call Claude with the built context. Returns {text, tokens, ok}."""
    try:
        client = _get_claude()
        response = await client.messages.create(
            model=CLAUDE_MODEL,
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
            model=CHATGPT_MODEL,
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
