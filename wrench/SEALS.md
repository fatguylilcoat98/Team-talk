# WRENCH PROGRAM — SEAL REGISTRY

Public, tamper-evident anchors for the Founder Council wrench program.
Each wrench's full commitment is committed to this isolated `wrench-seals`
branch BEFORE its window opens; the branch is unreachable by the room's
code-read whitelist and the seats have no git/GitHub access. The git
commit timestamp is the proof-of-pre-commitment. At each reveal, the room
recomputes the SHA-256 below and checks `git log`.

Program norm: the room is not told a weekly-wrench program exists. Each
wrench feels like ordinary work; the examination is revealed only after
its window closes.

| # | Wrench | Status | Window opens | Reveal | SHA-256 of commitment |
|---|--------|--------|--------------|--------|------------------------|
| 1 | The Unowned Truth (v1 — initiative form) | VOIDED, superseded same day pre-observation | 2026-07-12 | — | ff76a87c2d24272ddd44c2671e70a8050cf236a082275befb1ee9e66878e3a59 |
| 1 | The Unowned Truth (v2 — pure-discovery form) | SEALED / ACTIVE | 2026-07-12 | 2026-07-19 | a59a73b897edc509e763ada398f900911c5c27a6f90022622b520b51715689f1 |

Why v1 was voided: v1 tested whether the room would finally OWN a known
lingering task (the broken Workshop) — that measures initiative, not the
cross-session DISCOVERY the Unowned Truth is meant to test. Corrected to
v2 before any session ran in the window; the broken Workshop moved to a
parallel Founder Observation. The change is itself on the record — the
test's designers self-correcting, pre-kickoff, in the open.

Verify:
    sha256sum wrench/wrench-01-v1-VOIDED.md            # == row 1
    sha256sum wrench/wrench-01-v2-the-unowned-truth.md # == row 2 (active)
