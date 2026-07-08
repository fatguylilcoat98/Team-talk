"""Uploaded files (pictures + documents) stored on the server's disk.

Files land in uploads/ with a random id; an index.json maps ids to the
original name, type, and size. Images are passed to the AIs natively
(vision); text files and PDFs are extracted to text and inlined into
the round's context.
"""

import json
import mimetypes
import os
import re
import uuid
from typing import List, Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOADS_DIR = os.path.join(BASE_DIR, "uploads")
INDEX_PATH = os.path.join(UPLOADS_DIR, "index.json")

MAX_FILE_BYTES = 8 * 1024 * 1024   # 8MB — big phone photos fit; API limits nearby
MAX_TEXT_CHARS = 20000             # per file, inlined into context

IMAGE_MIMES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
TEXT_EXTS = {
    ".txt", ".md", ".csv", ".json", ".py", ".js", ".html", ".css",
    ".log", ".xml", ".yaml", ".yml", ".sh", ".ini", ".conf", ".toml",
}

_ID_RE = re.compile(r"^[a-f0-9]{32}$")


def _load_index() -> dict:
    try:
        with open(INDEX_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_index(index: dict) -> None:
    tmp = f"{INDEX_PATH}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2)
    os.replace(tmp, INDEX_PATH)


def classify(filename: str) -> Optional[str]:
    """'image' | 'pdf' | 'text' | None (unsupported)."""
    ext = os.path.splitext(filename.lower())[1]
    mime = mimetypes.guess_type(filename)[0] or ""
    if mime in IMAGE_MIMES:
        return "image"
    if ext == ".pdf":
        return "pdf"
    if ext in TEXT_EXTS:
        return "text"
    return None


def save_upload(filename: str, content: bytes) -> dict:
    """Store an uploaded file. Raises ValueError on unsupported/oversized."""
    safe_name = os.path.basename(filename or "file")[:120] or "file"
    kind = classify(safe_name)
    if kind is None:
        raise ValueError(
            "Unsupported file type. Send an image (png/jpg/gif/webp), a PDF, "
            "or a text file (.txt, .md, .csv, code, ...)."
        )
    if len(content) > MAX_FILE_BYTES:
        raise ValueError("File is too big — the limit is 8 MB.")
    if not content:
        raise ValueError("File is empty.")

    os.makedirs(UPLOADS_DIR, mode=0o700, exist_ok=True)
    file_id = uuid.uuid4().hex
    ext = os.path.splitext(safe_name.lower())[1]
    stored = f"{file_id}{ext}"
    with open(os.path.join(UPLOADS_DIR, stored), "wb") as f:
        f.write(content)

    mime = mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
    meta = {"id": file_id, "name": safe_name, "kind": kind, "mime": mime,
            "size": len(content), "stored": stored}
    index = _load_index()
    index[file_id] = meta
    _save_index(index)
    return meta


def get_meta(file_id: str) -> Optional[dict]:
    if not _ID_RE.match(file_id or ""):
        return None
    return _load_index().get(file_id)


def get_path(file_id: str) -> Optional[str]:
    meta = get_meta(file_id)
    if not meta:
        return None
    path = os.path.join(UPLOADS_DIR, os.path.basename(meta["stored"]))
    return path if os.path.exists(path) else None


def load_bytes(file_id: str) -> Optional[bytes]:
    path = get_path(file_id)
    if not path:
        return None
    with open(path, "rb") as f:
        return f.read()


def extract_text(file_id: str) -> str:
    """Text content of a text/PDF upload, truncated for context."""
    meta = get_meta(file_id)
    if not meta:
        return ""
    if meta["kind"] == "text":
        raw = load_bytes(file_id) or b""
        text = raw.decode("utf-8", errors="replace")
    elif meta["kind"] == "pdf":
        try:
            from pypdf import PdfReader
            reader = PdfReader(get_path(file_id))
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception as e:
            return f"(could not read PDF {meta['name']}: {e})"
    else:
        return ""
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS] + f"\n... (truncated — file continues, {meta['size']} bytes total)"
    return text


def attachments_context(metas: List[dict]) -> str:
    """The ATTACHED FILES section for the current round's context."""
    parts = []
    for m in metas:
        if m["kind"] == "image":
            continue  # images go to the API natively, not as text
        body = extract_text(m["id"])
        parts.append(f"--- {m['name']} ---\n{body}")
    if not parts:
        return ""
    return "=== ATTACHED FILES (from Chris, this round) ===\n" + "\n\n".join(parts)
