# WRENCH PROGRAM — SEAL REGISTRY

Public, tamper-evident anchors for the Founder Council wrench program.
Each wrench's full commitment is written and committed to this isolated
`wrench-seals` branch BEFORE its window opens; the branch is unreachable
by the room's code-read whitelist (subdirectory, not top-level .py or the
whitelisted static files) and the seats have no git/GitHub access. The
git commit timestamp is the proof-of-pre-commitment. At each reveal, the
room recomputes the SHA-256 below and checks `git log`.

| # | Wrench | Window opens | Reveal | SHA-256 of commitment |
|---|--------|--------------|--------|------------------------|
| 1 | The Unowned Truth | 2026-07-12 | 2026-07-19 | ff76a87c2d24272ddd44c2671e70a8050cf236a082275befb1ee9e66878e3a59 |

Verify wrench #1:
    sha256sum wrench/wrench-01-the-unowned-truth.md
    # must equal the hash in the row above
