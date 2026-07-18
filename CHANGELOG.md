# Changelog

## 2026-07-18

- **Defense-in-depth: prevent orchestration scaffolding from persisting in Team Talk transcripts.** Investigation confirmed seat isolation was already functioning correctly (`MODEL ECHO WITHOUT PIPELINE LEAK`). Added transcript scrubbing and regression tests to prevent orchestration-template echoes from re-entering future conversations. (PR #56, merge `b2b74f9`)
