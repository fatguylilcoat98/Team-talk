# Reasoning Ledger — Layer 0 / Layer 1

A versioned, append-only reasoning graph. This document explains the one
boundary that matters: what is **stored** (Layer 0) versus what is
**computed** (Layer 1).

## Layer 0 — the graph (persistent)

Files: `reasoning_store.py` → `memory/reasoning_claims.jsonl`,
`memory/reasoning_participations.jsonl`.

- **Claim** — the durable identity of an idea. Not owned by a seat; many
  seats may participate in one Claim. It carries no history of its own
  (no retries, revivals, citations, or parentage live on the Claim).
- **Participation** — one immutable action a seat took in relation to a
  Claim. Always `type: "assert"`. A retry is **not** a new type.
- **Reference** — a typed edge *carried by* a Participation
  (`{type, target_type, target_id}`). Edges are not separate stored
  objects.

Append-only, like the Glass Box: no update, no delete, no rewrite exists
in the module. A broken reference is **accepted and preserved exactly as
written** — invalidity is never a write-time rejection. Corrections are
new Participations, never edits. Each append also emits a hash-chained
`ledger.py` event (`claim_created` / `participation_appended`), so the
graph inherits the room's tamper-evidence.

**Retry identity has one source of truth: the `retry_of` edge.** A marked
retry is an `assert` Participation on the same Claim, carrying exactly one
`retry_of` reference to the *original Participation id* (never the Claim
id). It does not mint a new Claim and does not touch the original.

## Layer 1 — mechanical observations (computed, never stored)

File: `reasoning_observations.py`. `observe(participations)` returns
current mechanical facts derived from graph state. Observations are **not**
Claims, **not** Participations, and are **never written** to any ledger or
store. Recompute and they track the graph: a dangling reference stops
dangling the moment its target arrives.

The validator's **entire jurisdiction is the Reference Type Registry**
(`reference_registry.py`). It runs only the checks the registry declares
for a type, and every observation carries the `registry_version` that
produced it. For `retry_of` (registry v1) the finite checks are:

- `target_exists` → else `dangling_reference` (`target_not_found`)
- `target_is_participation` → else `invalid_reference` (`wrong_target_type`)
- `target_is_not_self` → else `invalid_reference` (`target_is_self`)
- cardinality ≤ 1 → else `invalid_reference` (`cardinality_exceeded`)

Plus one structural check: a Participation that carries a **non-semantic**
resend signal (`declared_resend_of`) but no matching `retry_of` edge yields
`missing_expected_reference`. The pairing signal is a caller-supplied
pointer or delivery id — **never** content similarity.

### `retry_of` (edge) vs `declared_resend_of` (signal) — read this before changing anything

This is the single most misreadable decision in the slice. They look
redundant. They are not.

- **`retry_of`** is a reference *edge* on the participation — the graph's
  actual, authoritative truth that this is a retry of a specific original.
- **`declared_resend_of`** is a *field* holding a non-semantic claim from
  the caller ("a resend happened, of X") — a signal, not an edge.

They are **allowed to disagree, on purpose.** A `declared_resend_of` with
no matching `retry_of` edge is precisely the "unmarked retry," and Layer 1
surfacing it is the whole reason this code exists. The tempting
"simplification" — auto-deriving the edge from the declaration so they can
never disagree — would silently delete the detectability this slice was
built to guarantee. `append_from_retry_signal` maps a *resolvable* signal
to an edge on the way IN; it never rewrites history to hide a dropped edge.

## The line that must not be crossed

Layer 1 states mechanical facts only. It must **never** say a marker was
omitted on purpose, that a seat concealed a resend, that two messages mean
the same thing, or that a missing edge proves dishonesty. Those are
Layer 2 (reasoning) and Layer 3 (governance), out of scope here. The
registry records these forbidden questions explicitly as each type's
`interpretation_boundary`, so the boundary lives in the data.

## The promise

> Invalid or missing retry relationships may exist, but they must remain
> mechanically detectable.

## `is_retry` → `retry_of`

`reasoning_store.append_from_retry_signal(...)` is the mapping seam. A
declared resend with a resolvable original becomes a proper `retry_of`
edge (a marked retry, silent under Layer 1). A declared resend whose
original cannot be resolved is still preserved, with `declared_resend_of`
set to the `UNRESOLVED_TARGET` sentinel, so Layer 1 reports "target could
not be resolved" (`missing_expected_reference`, `expected_target: null`)
rather than guessing.

## Schema versioning

Every persisted Claim and Participation carries `schema_version`
(`reasoning_store.SCHEMA_VERSION`, currently 1). It costs nothing now and
lets a future format change be migrated by branching on the version
instead of guessing a row's age. Observations are ephemeral and instead
carry `registry_version` (the contract that produced them).

## Live wiring (Workshop)

`workshop_reasoning.py` connects the Workshop to the graph, and `app.py`
calls it at the two sites where it already owns ledger events:

- **Target set** (`set_workshop_target`): the target's goal opens one Claim
  (`open_target_claim`), whose id is stored on the workshop state
  (`reasoning_claim_id`) so the session's turns attach to it.
- **Each cycle** (`_workshop_cycle_task`, inside the same lock that
  serializes cycles): every content-bearing turn (landed / pending /
  rejected) becomes an `assert` Participation (`record_cycle`), with the
  turn's disposition kept as a fact in the participation's `meta` (not
  baked into content).

**Retries are not inferred.** Same seat + same Claim proves only "later,"
not "retry" — a later turn may be a refinement, a rebuttal, new evidence,
or a fresh contribution. The Workshop has no genuine non-semantic retry
signal today (a bench turn is only a PASS or an EDIT; the version chain's
`prev_hash` is chronological, not an attempt pointer), so **normal turns
are left unlinked and live retry emission is not yet wired**. The seam is
ready: when a real signal exists (an explicit retry flag, a resend id, or a
caller-supplied original participation id), pass it through
`record_turn(..., is_retry=True, original_participation_id=...)` and only
then is a `retry_of` edge created — via `append_from_retry_signal`, never
guessed from content or order.

**Developer view:** `GET /api/workshop/reasoning` returns the Claims,
Participations, and the Layer-1 observations computed on demand
(`workshop_reasoning.snapshot`). `?scope=all` shows the whole graph; the
default scopes to the current target's Claim. Observations are derived at
request time and never persisted.

End-to-end coverage is in `tests/test_workshop_reasoning.py` (runs without
FastAPI or any AI provider).

## Tests

`tests/test_reasoning_ledger.py` (standalone: `python tests/test_reasoning_ledger.py`).
The essential receipt: the derived observation fires on the planted
unmarked retry and stays silent on the correctly marked retry.
