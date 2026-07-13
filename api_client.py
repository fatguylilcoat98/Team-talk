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

import asyncio
import os
import random
import re
import time
import uuid
from typing import Optional

import anthropic
import openai
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

import failure_log
import settings_store

MAX_TOKENS = int(os.getenv("MAX_TOKENS_CLAUDE", os.getenv("MAX_TOKENS", "2000")))
# Retry budget when a reasoning model burns all of MAX_TOKENS thinking and
# emits no visible text (Muse Spark, DeepSeek R1, o-series, ...).
REASONING_MAX_TOKENS = int(os.getenv("MAX_TOKENS_REASONING", "8000"))
API_TIMEOUT = float(os.getenv("API_TIMEOUT", "30"))

# --- Retry policy: the room's own spec (Night Shift run #1 + the time-bounds
# --- round that followed). Classes and rules are theirs; receipts below.
#
# Terminal — a second identical call produces the identical failure, so a
# retry is spending money to fail twice:
#   no_api_key, auth_rejected, bad_request, model_not_found, out_of_credits,
#   empty_reasoning (a budget event, not an error — distinct receipt).
# Retryable — can succeed on a repeat:
RETRYABLE = {"rate_limit", "connection_failure", "timeout", "provider_status_error"}
RETRY_MAX_ATTEMPTS = 3          # 1 initial + 2 retries, attempt is 1-based
# Per-attempt timeouts are TIERED BY CALLER CONTEXT, passed — never inferred
# (Claude's rule). Healthy generation here runs ~10s to ~120s+; the first
# night's 30s/10s numbers would have killed most legitimate calls.
PER_ATTEMPT_TIMEOUT = {"chat": 150.0, "workshop": 300.0, "night": 300.0}
CEILING_BUFFER = 60.0           # wall ceiling = attempts × per-attempt + this

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

# A reply cut off by the token cap (finish_reason "length") with less than this
# much visible text is a reasoning STUB, not an answer: the budget went to
# hidden thinking and the model got cut off mid-word (Muse Spark's "Glass is
# up - I"). Escalate it or surface the honest error — never pass the fragment
# off as a real reply. A genuinely long reply that hit the cap keeps its text.
_STUB_TEXT_CHARS = 400


def _starved_stub(text: str, choice) -> bool:
    return (getattr(choice, "finish_reason", "") == "length"
            and len((text or "").strip()) < _STUB_TEXT_CHARS)


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


def _retry_after(e: Exception) -> Optional[float]:
    """Retry-After seconds from the provider's response headers, if sane."""
    try:
        val = e.response.headers.get("retry-after")
        return float(val) if val is not None else None
    except (AttributeError, TypeError, ValueError):
        return None


def _rate_limit_message(name: str, e: Exception) -> str:
    # Providers return 429 both for real rate limits and for accounts with
    # a $0 balance; the latter usually carries "insufficient_quota".
    if "insufficient_quota" in str(e):
        return OPENAI_NO_CREDITS_MSG
    return f"Error: {name}'s API is rate limited — wait a moment and retry."


async def call_participant(p: dict, system: str, prompt: str, images: list = None,
                           max_tokens: int = None, context: str = "chat",
                           run_id: str = None, session_id: str = None,
                           run_budget_remaining: int = None) -> dict:
    """Call one AI with retry — the room's spec, implemented to the letter.

    context: "chat" | "workshop" | "night" — PASSED at the call site, never
    inferred (Claude's rule). Picks the per-attempt timeout tier.
    run_budget_remaining: Night Shift only — before each RETRY, if this is
    smaller than the call's output cap, the retry is budget_blocked.

    Returns {text, tokens, ok} — never raises. A recovered retry returns
    only the clean reply; the failure log holds the scar tissue.
    """
    name = p.get("name", "AI")
    call_id = uuid.uuid4().hex
    per_attempt = PER_ATTEMPT_TIMEOUT.get(context, PER_ATTEMPT_TIMEOUT["chat"])
    deadline = time.monotonic() + RETRY_MAX_ATTEMPTS * per_attempt + CEILING_BUFFER
    budget_cap = max_tokens or p.get("max_tokens") or MAX_TOKENS
    prior_failures = 0
    total_tokens = 0

    def _log(attempt: int, res: dict, latency_ms: int, final: bool,
             recovered: bool = False, budget_blocked: bool = False,
             ra_exceeded: bool = False):
        failure_log.log_attempt({
            "call_id": call_id, "run_id": run_id, "session_id": session_id,
            "seat": p.get("id") or name, "provider": p.get("provider"),
            "model": p.get("model"), "error_class": res.get("error_class"),
            "http_status": res.get("http_status"), "attempt": attempt,
            "max_attempts": RETRY_MAX_ATTEMPTS, "final": final,
            "recovered": recovered, "latency_ms": latency_ms,
            "retry_after_used": res.get("retry_after"),
            "budget_blocked": budget_blocked,
            "retry_after_exceeded_ceiling": ra_exceeded,
            "msg_trunc": res.get("text", ""),
        })

    for attempt in range(1, RETRY_MAX_ATTEMPTS + 1):
        start = time.monotonic()
        res = await _attempt(p, system, prompt, images, max_tokens, per_attempt)
        latency_ms = int((time.monotonic() - start) * 1000)
        total_tokens += res.get("tokens", 0) or 0

        if res["ok"]:
            if prior_failures:
                # recovered:true iff ≥1 prior recovered:false for this
                # call_id — a clean first-attempt success writes zero lines.
                _log(attempt, {"error_class": res.get("error_class")},
                     latency_ms, final=False, recovered=True)
            return {"text": res["text"], "tokens": total_tokens, "ok": True}

        eclass = res.get("error_class") or "unknown"
        final = eclass not in RETRYABLE or attempt >= RETRY_MAX_ATTEMPTS
        wait = 0.0
        budget_blocked = False
        ra_exceeded = False
        if not final:
            ra = res.get("retry_after")
            if eclass == "rate_limit" and ra is not None:
                # honor Retry-After only if sleep + a full attempt still
                # fits under the ceiling (Claude's R4 fix)
                if time.monotonic() + ra + per_attempt > deadline:
                    ra_exceeded = True
                    final = True
                else:
                    wait = float(ra)
            else:
                wait = (1.0, 2.0)[min(attempt - 1, 1)] + random.uniform(0, 0.5)
            if not final and time.monotonic() + wait + per_attempt > deadline:
                final = True    # the ceiling wins over the attempt count
            if not final and run_budget_remaining is not None \
                    and run_budget_remaining < budget_cap:
                budget_blocked = True
                final = True
        prior_failures += 1
        _log(attempt, res, latency_ms, final=final,
             budget_blocked=budget_blocked, ra_exceeded=ra_exceeded)

        if final:
            if eclass == "empty_reasoning" or (attempt == 1 and eclass not in RETRYABLE):
                # Terminal on first sight — the original message already
                # says exactly what happened; "failed after 1" adds noise.
                text = res["text"]
            else:
                text = (f"Error: {name} failed after {attempt} attempt"
                        f"{'s' if attempt > 1 else ''} — {eclass} "
                        f"(id={call_id[:8]}). {res['text'][7:].lstrip() if res['text'].startswith('Error:') else res['text']}")
            return {"text": text, "tokens": total_tokens, "ok": False}
        await asyncio.sleep(wait)

    # unreachable — the loop always returns — but never raise past here
    return {"text": f"Error: {name} failed (id={call_id[:8]}).", "tokens": total_tokens, "ok": False}


async def _attempt(p: dict, system: str, prompt: str, images: list,
                   max_tokens: int, per_attempt: float) -> dict:
    """ONE attempt. error_class is assigned STRUCTURALLY in each typed
    except branch, before any human "Error:" string is built — parsing the
    string is forbidden (the room's load-bearing mandate, night #1 R2).

    Returns {text, tokens, ok, error_class, http_status, retry_after}.
    """
    # Budget order: explicit caller override (Workshop bench) > the seat's
    # own cost cap from Settings > the global default. A seat cap is a hard
    # ceiling Chris chose — it also wins over the reasoning-model escalation
    # below, because its whole point is bounding worst-case spend.
    seat_cap = p.get("max_tokens")
    call_budget = max_tokens or seat_cap or MAX_TOKENS
    name = p.get("name", "AI")
    key = participant_key(p)
    if not key:
        return {
            "text": f"Error: no API key configured for {name} — add one in Settings.",
            "tokens": 0, "ok": False,
            "error_class": "no_api_key", "http_status": None, "retry_after": None,
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
            # Prompt caching: the system prompt (room rules, truth layer,
            # modes) is identical across a seat's calls within a session.
            # cache_control makes Anthropic bill repeats at ~10% instead of
            # full price — Chris's dashboard showed a 0% hit rate while the
            # Claude seat was his most expensive by 10x. Cache TTL is 5 min,
            # which live rounds easily beat. Zero behavior change.
            response = await client.messages.create(
                model=p["model"],
                max_tokens=call_budget,
                system=[{"type": "text", "text": system,
                         "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": content}],
                timeout=per_attempt,
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
            if max_tokens:
                budget = max_tokens  # explicit caller override is exact
            elif seat_cap:
                budget = seat_cap    # Chris's cap is absolute — no escalation
            else:
                budget = max(call_budget,
                             REASONING_MAX_TOKENS if p["model"] in _reasoning_models else 0)
            response = await client.chat.completions.create(
                model=p["model"], max_tokens=budget, messages=messages,
                timeout=per_attempt,
            )
            choice = response.choices[0]
            text = choice.message.content or ""
            tokens = response.usage.completion_tokens if response.usage else 0
            if _starved_stub(text, choice) \
                    and budget < REASONING_MAX_TOKENS and not seat_cap:
                # (A seat with an explicit cost cap never escalates — the cap
                # is Chris bounding worst-case spend; a starved reply under it
                # returns the honest error instead of a surprise bill.)
                # Reasoning models (Muse Spark, DeepSeek R1, o-series) can burn
                # the whole budget on internal thinking and get cut off before
                # writing a single visible word — or after only a fragment of
                # one. Retry once with real headroom — and remember, so future
                # calls skip the doomed first attempt.
                _reasoning_models.add(p["model"])
                response = await client.chat.completions.create(
                    model=p["model"], max_tokens=REASONING_MAX_TOKENS, messages=messages,
                    timeout=per_attempt,
                )
                choice = response.choices[0]
                text = choice.message.content or ""
                tokens += response.usage.completion_tokens if response.usage else 0
            # A fragment cut off by the cap is not a reply — surface the honest
            # error (which the room handles) instead of storing half a sentence.
            if _starved_stub(text, choice):
                text = ""
        if not text.strip():
            return {
                "text": (
                    f"Error: {name} returned no text within its token budget — the model "
                    f"likely spent it all on internal reasoning. Raise the cap in Settings "
                    f"→ {name} → Advanced, or switch to a non-reasoning model."
                ),
                "tokens": tokens, "ok": False,
                "error_class": "empty_reasoning", "http_status": None, "retry_after": None,
            }
        return {"text": text, "tokens": tokens, "ok": True,
                "error_class": None, "http_status": None, "retry_after": None}
    except (anthropic.RateLimitError, openai.RateLimitError) as e:
        eclass = "out_of_credits" if "insufficient_quota" in str(e) else "rate_limit"
        return {"text": _rate_limit_message(name, e), "tokens": 0, "ok": False,
                "error_class": eclass, "http_status": 429,
                "retry_after": _retry_after(e)}
    except (anthropic.AuthenticationError, openai.AuthenticationError):
        return {"text": f"Error: {name}'s API key was rejected (invalid or revoked).",
                "tokens": 0, "ok": False,
                "error_class": "auth_rejected", "http_status": 401, "retry_after": None}
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
                "error_class": "bad_request", "http_status": 400, "retry_after": None,
            }
        return {"text": f"Error: {name}'s API rejected the request (400): {msg}",
                "tokens": 0, "ok": False,
                "error_class": "bad_request", "http_status": 400, "retry_after": None}
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
            "error_class": "model_not_found", "http_status": 404, "retry_after": None,
        }
    except (anthropic.APIStatusError, openai.APIStatusError) as e:
        return {"text": f"Error: {name}'s API returned an error ({e.status_code}): {getattr(e, 'message', e)}",
                "tokens": 0, "ok": False,
                "error_class": "provider_status_error",
                "http_status": getattr(e, "status_code", None),
                "retry_after": _retry_after(e)}
    except (anthropic.APITimeoutError, openai.APITimeoutError):
        return {"text": f"Error: {name}'s API did not answer within {int(per_attempt)}s.",
                "tokens": 0, "ok": False,
                "error_class": "timeout", "http_status": None, "retry_after": None}
    except (anthropic.APIConnectionError, openai.APIConnectionError):
        return {"text": f"Error: could not reach {name}'s API — check the server's network / base URL.",
                "tokens": 0, "ok": False,
                "error_class": "connection_failure", "http_status": None, "retry_after": None}
    except Exception as e:
        return {"text": f"Error: {e}", "tokens": 0, "ok": False,
                "error_class": "unknown", "http_status": None, "retry_after": None}


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
