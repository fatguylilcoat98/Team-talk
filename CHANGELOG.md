# Changelog

## 2026-07-19

- **Phase 1 integration: blind experiments and the Pattern Catcher are now reachable in the live room.** Both mechanisms shipped earlier (`f15c5a4`, `f5ce93d`, `72354b5`) as built-and-tested but unwired. This wires them in: `modes: ["blind"]` now opens a sealed, experiment-scoped blind window instead of the old cosmetic per-session labels — scoped history, an outbound prompt audit, and an inbound header-strip/prose-compromise guard, all fail-closed. A `LEDGER:` line from whoever holds the Pattern Catcher office now runs a real read-only search and delivers on the seat's next turn, same convention as mail and receipts. Exports (html/markdown/pdf) stay anonymous before a manual reveal and resolve to "Voice N — Name" after. Neither mechanism runs unless invoked: blind mode requires the mode flag, the Pattern Catcher requires an assigned office holder. Normal mode is unchanged. 218 tests across 16 suites, including live-turn-path integration tests against the real `/api/chat` and export endpoints.

## 2026-07-18

- **Defense-in-depth: prevent orchestration scaffolding from persisting in Team Talk transcripts.** Investigation confirmed seat isolation was already functioning correctly (`MODEL ECHO WITHOUT PIPELINE LEAK`). Added transcript scrubbing and regression tests to prevent orchestration-template echoes from re-entering future conversations. (PR #56, merge `b2b74f9`)
