# FLINT Cognitive Reflection Layer — design (v1)

*(supersedes docs/PRG_DESIGN.md — the PRG work is Stage 1 of this layer)*

## Purpose

A modular, deterministic reflection framework that runs immediately before an
AI response is finalized. It gives a model structured information about its own
prior statements, unresolved claims, available receipts, hidden assumptions,
attribution history, and whether its wording is stronger than its evidence.

It does **not** tell a model what to believe. Cognitive assistance, not
censorship, and not a replacement for reasoning — "spellcheck for reasoning."

## Privacy boundary (read first)

> The Reflection Layer does not reveal or request private chain-of-thought.
> It evaluates the **final draft** and **structured historical records** only.

No hidden reasoning is stored or requested. No model calls, no network, no
embeddings. Stored excerpts are short and bounded (≤160 chars); full messages,
prompts, keys, and provider metadata are never copied into reflection records.

## Architecture

```
reflection/
    __init__.py      public surface
    lexicon.py       shared deterministic matchers (regex + token math)
    models.py        ReflectionContext / Warning / PassResult / Result
    engine.py        run passes in order, isolate failures, aggregate
    registry.py      explicit DEFAULT_PASSES (no dynamic loading)
    store.py         separate append-only JSONL + read-only analytics
    flags.py         feature flags (default OFF; shadow default ON)
    hook.py          flag-gated shadow send-path integration
    passes/
        base.py      ReflectionPass interface
        mirror.py    Mirror Pass
        assumption.py Assumption Pass
        confidence.py Confidence Pass
```

A pass receives a `ReflectionContext`, returns `list[ReflectionWarning]`, and
must never modify the draft, write storage, call a model, or hit the network.
The engine runs registered passes in deterministic order and wraps each one, so
one failing pass never stops the others.

## The three passes

- **Mirror** — "have I said/flagged/learned something that conflicts with this?"
  Prior contradiction (red, opposite polarity on a firm prior claim), repeated
  unresolved claim (yellow), prior correction (yellow, a receipt resolving the
  claim false), attribution mismatch (red, **only** with an explicit supplied
  ledger map — authorship is never guessed from prose). Quoted text is judged on
  the author's own (unquoted) words.
- **Assumption** — "what conclusion depends on an unverified assumption?" Flags
  strong causal/conclusion language for which no supplied receipt on the same
  topic *and same polarity* exists. Central distinction: an observation may be
  supported while the causal conclusion drawn from it is not. Yellow normally;
  red only for a strong causal assertion with supplied competing evidence. It
  invents no facts and no alternative explanations.
- **Confidence** — "is the wording stronger than the evidence?" Unsupported
  certainty (yellow, strong markers with no on-topic receipt) and confidence
  drift (yellow; hedged earlier → certain now with no new receipt; red if a
  ledger receipt contradicts). Quoted certainty is not the author's certainty.
  `detector_confidence` is the heuristic's own match confidence — never a
  numeric epistemic probability.

## Data contracts

`ReflectionContext(author, draft_text, participation_id, prior_participations,
prior_claims, unresolved_claims, receipts, attribution_map, timestamp,
metadata)` — all optional fields degrade to empty; the engine never infers
evidence not supplied.

`ReflectionWarning(warning_id, pass_name, severity, category, message,
current_excerpt, prior_excerpt, source_reference, detector_confidence,
metadata)`. Stable categories: `prior_contradiction`,
`repeated_unresolved_claim`, `prior_correction`, `attribution_mismatch`,
`unsupported_assumption`, `unsupported_causal_claim`,
`confidence_exceeds_evidence`, `confidence_drift`.

`ReflectionResult(schema_version, reflection_id, author, timestamp,
participation_id, overall_severity, pass_results[], warnings[], matched_claims[],
confidence_delta, revision_performed, shadow_mode, engine_version, duration_ms)`.

## Feature flags & shadow mode

```
REFLECTION_LAYER_ENABLED      default: false
REFLECTION_LAYER_SHADOW_MODE  default: true
```

Settings values (`reflection_layer_enabled` / `_shadow_mode`) override env.

- **OFF** — the hook does one env check and returns. Reflection code is never
  invoked; nothing is stored; response text, prompts, seat isolation, sessions,
  and the reasoning ledger are byte-identical to today.
- **Shadow ON** — evaluate each finalized response against that author's own
  prior session messages, append a `ReflectionResult` to the reflection store,
  and return. It shows nothing to model or user, revises nothing, blocks
  nothing, mutates nothing. Any exception is swallowed — reflection can never
  prevent a reply from being sent or stored.

Send-path hook location: `app.py`, `_chat_impl`, immediately after `round_data`
is assembled and **before** `session["rounds"].append` / `save_session`.

## Severity policy

`green` (no meaningful warning), `yellow` (unresolved repetition, unsupported
assumption, moderate drift, missing receipt for strong wording), `red` (polarity
contradiction with a supplied prior, contradiction with a resolved receipt,
ledger-backed attribution mismatch, strong assertion materially contradicted by
supplied evidence). Overall severity is the worst warning.

## False-positive philosophy

Prefer missing a weak warning to flooding the room. Detectors are lexical
heuristics with conservative thresholds (Jaccard/containment ≥ ~0.45–0.5, ≥ 2
repeats); red is reserved for polarity flips or ledger-backed facts; ledger
checks fire only when data is supplied. The layer ships OFF and runs first in
shadow mode so the false-positive rate (a human-labeled metric) is measured
before any UI is shown.

## Storage behavior

`memory/reflections.jsonl` — append-only, one JSON object per line, atomic
single-line appends, no update, no delete. Malformed historical lines are
tolerated and never block a future append. Missing directory is created;
write failures degrade to no-op. Never touches the reasoning ledger or sessions.
Read-only analytics: totals, by severity, by pass, by category, authors warned,
revision rate, average duration.

## Future UI contract (not built in v1)

```
Reflection Layer
  Mirror Pass:     yellow
  Assumption Pass: red
  Confidence Pass: yellow
  Actions:  Continue   Revise   Explain
```

One optional revision only; no recursive review loop; the panel explains each
warning from its stored `source_reference` / excerpts. No automatic rewriting;
no message is ever blocked in v1.

## Future plugin passes

Added by registering explicitly in `registry.py` — no engine redesign. Candidates:
Memory Verification, Missing Information, Perspective, Question, Silence,
Outcome, Regret, Pattern. Dynamic loading from arbitrary files is deliberately
avoided.

## Test strategy

`tests/test_reflection.py` (31 tests): per-pass detection and non-detection,
observation/conclusion distinction, quoted-text handling, engine order + failure
isolation + malformed-input safety, store append-only/tolerance/analytics/failure,
flags default-OFF and byte-identical, shadow-ON stores + byte-identical response,
reflection-failure isolation, and the two forensic fixtures (Claude leak
accusation, Gemini attribution).

## Performance

3-pass `reflect()`: median ~1.5 ms, max ~3.0 ms on a 328-char draft with 40
prior messages and 10 receipts (venv Python 3.14). Well under the 5 ms target.
No I/O in the engine.

## Rollout plan

1. **Stage 1 (done):** engine, store, tests (PRG).
2. **Stage 2 (this task):** three modular passes + flag-gated shadow send-path
   integration.
3. **Stage 3 (later):** run real sessions in shadow mode; inspect warning
   usefulness, false-positive rate, red precision, per-pass value, performance.
4. **Stage 4 (later):** UI panel (Continue / Revise / Explain) with one optional
   revision. No automatic rewriting; no blocking in v1.
