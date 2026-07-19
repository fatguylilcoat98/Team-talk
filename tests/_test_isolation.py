"""Shared helper for tests that drive the REAL /api/chat turn path via
FastAPI's TestClient. Not a test file itself (no test_ prefix picked up by
the suite runner) — imported by tests that need it.

`redirect_all_stores(tmp)` re-points every store module's on-disk path at a
throwaway temp dir, the same technique test_ledger_query.py and
test_reasoning_ledger.py use for a single store, just applied to every store
`_chat_impl` can reach on a live turn — so a full end-to-end call through
app.chat() never touches this laptop's real memory/, sessions/, or
workshop/ directories.
"""

import os


def redirect_all_stores(tmp: str) -> None:
    import about_store, blind_experiment, brain, choice_store, code_access
    import crt_store, episode_store, failure_log, file_store, history_store
    import journal_store, ledger, ledger_query, mailbox_store, memory_store
    import mission_store, notebook_store, office_store, pattern_catcher
    import proposal_store, questions_store, receipt_store, scratch_store
    import session_manager, settings_store, studio_store, wall_store, workshop_store

    ledger.LEDGER_DIR = tmp
    ledger.LEDGER_PATH = os.path.join(tmp, "ledger.jsonl")

    session_manager.SESSIONS_DIR = os.path.join(tmp, "sessions")
    os.makedirs(session_manager.SESSIONS_DIR, exist_ok=True)
    # ledger_query.py computes its OWN SESSIONS_DIR from its own file location
    # — same directory as session_manager's by default, so it must be
    # redirected separately or the Pattern Catcher would search real sessions.
    ledger_query.SESSIONS_DIR = session_manager.SESSIONS_DIR

    office_store.STORE_DIR = tmp
    office_store.OFFICES_PATH = os.path.join(tmp, "offices.jsonl")

    blind_experiment.STORE_DIR = tmp
    blind_experiment.EXPERIMENTS_PATH = os.path.join(tmp, "blind_experiments.jsonl")

    pattern_catcher.STORE_DIR = tmp
    pattern_catcher.QUERIES_PATH = os.path.join(tmp, "pattern_catcher_queries.json")

    mailbox_store.MAILBOX_DIR = tmp
    mailbox_store.MAILBOX_PATH = os.path.join(tmp, "mailbox.json")

    receipt_store.RECEIPTS_DIR = tmp
    receipt_store.RECEIPTS_PATH = os.path.join(tmp, "receipts.json")

    journal_store.JOURNALS_DIR = os.path.join(tmp, "journals")

    code_access.PENDING_PATH = os.path.join(tmp, "code_reads.json")

    choice_store.CHOICE_DIR = os.path.join(tmp, "choice")

    scratch_store.SCRATCH_PATH = os.path.join(tmp, "scratch.json")

    memory_store.MEMORY_DIR = tmp
    memory_store.MEMORY_PATH = os.path.join(tmp, "memory.json")

    notebook_store.NOTEBOOK_DIR = tmp
    notebook_store.NOTEBOOK_PATH = os.path.join(tmp, "notebook.json")

    questions_store.QUESTIONS_DIR = tmp
    questions_store.QUESTIONS_PATH = os.path.join(tmp, "questions.json")

    about_store.ABOUT_DIR = tmp
    about_store.ABOUT_PATH = os.path.join(tmp, "about.json")

    proposal_store.STORE_DIR = tmp
    proposal_store.STORE_PATH = os.path.join(tmp, "proposals.json")

    studio_store.STORE_DIR = tmp
    studio_store.STORE_PATH = os.path.join(tmp, "studio.json")

    crt_store.CRT_PATH = os.path.join(tmp, "crt.json")

    episode_store.EPISODES_DIR = tmp
    episode_store.EPISODES_PATH = os.path.join(tmp, "episodes.json")

    history_store.HISTORY_DIR = tmp
    history_store.HISTORY_PATH = os.path.join(tmp, "history.json")

    wall_store.WALL_DIR = tmp
    wall_store.WALL_PATH = os.path.join(tmp, "wall.json")

    mission_store.STORE_DIR = tmp
    mission_store.STORE_PATH = os.path.join(tmp, "missions.json")

    workshop_dir = os.path.join(tmp, "workshop")
    workshop_store.WORKSHOP_DIR = workshop_dir
    workshop_store.STATE_PATH = os.path.join(workshop_dir, "state.json")
    workshop_store.VERSIONS_DIR = os.path.join(workshop_dir, "versions")
    workshop_store.CHAIN_PATH = os.path.join(workshop_dir, "versions.jsonl")
    workshop_store.ARTIFACT_DIR = os.path.join(workshop_dir, "current")

    file_store.UPLOADS_DIR = os.path.join(tmp, "uploads")
    file_store.INDEX_PATH = os.path.join(file_store.UPLOADS_DIR, "index.json")

    settings_store.CONFIG_DIR = os.path.join(tmp, "config")
    settings_store.SETTINGS_PATH = os.path.join(settings_store.CONFIG_DIR, "settings.json")

    brain.CACHE_DIR = tmp
    brain.CACHE_PATH = os.path.join(tmp, "embeddings.json")

    log_dir = os.path.join(tmp, "logs")
    failure_log.LOG_DIR = log_dir
    failure_log.LOG_PATH = os.path.join(log_dir, "failures.jsonl")
    failure_log.ARCHIVE_DIR = os.path.join(log_dir, "archive")


ROSTER = [
    {"id": "claude", "name": "Claude", "provider": "anthropic", "model": "claude-opus",
     "color": "#d97757", "resting": False},
    {"id": "gemini", "name": "Gemini", "provider": "google", "model": "gemini-2.5",
     "color": "#4285f4", "resting": False},
    {"id": "flint", "name": "FLINT", "provider": "ollama", "model": "llama3.1:8b",
     "color": "#7a7a7a", "resting": False},
]


def make_stub_call_participant(reply_text=None, per_participant=None):
    """A fake api_client.call_participant that never touches the network.

    `reply_text`: same reply for everyone (string, or a callable taking the
    participant dict and returning a string). `per_participant`: dict of
    participant id -> reply text/callable, takes precedence when given.
    Every call is recorded on `calls` (as (participant_id, system, prompt))
    so a test can inspect exactly what would have been sent.
    """
    calls = []

    async def _stub(p, system, prompt, images=None, context="chat", session_id=None):
        calls.append((p["id"], system, prompt))
        src = None
        if per_participant and p["id"] in per_participant:
            src = per_participant[p["id"]]
        elif reply_text is not None:
            src = reply_text
        text = src(p) if callable(src) else (src if src is not None else f"({p['name']} reply)")
        return {"ok": True, "text": text, "tokens": len(text.split())}

    _stub.calls = calls
    return _stub
