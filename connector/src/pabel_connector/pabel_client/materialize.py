"""Local, one-shot materialized copies of a read_document result. Deliberately
NOT kept fresh after being written: once on disk, a copy is an ordinary local
file PABEL no longer governs - no re-verification on later access, no write
protection, no tracking of the source it came from. The only guarantee this
module provides is disposal (purge_all(), called unconditionally by Claude
Code's SessionEnd hook - see adapters/claude_code.py), not staleness detection.

This is a deliberate scope decision, not an oversight: real mid-session
freshness enforcement would need the server to start keeping a document store
of its own (today explicitly stateless) plus an always-on local process able to
receive a push notification even between sessions (nothing like that exists in
this package - every process here is short-lived: the hook runs once per tool
call, this MCP server runs only for one session's duration). See
docs/phase2-engineering-notes.md for the full reasoning.
"""

import json
import os
import shutil
import uuid
from pathlib import Path

from .relay import read_document_with_login_async

CACHE_ROOT = Path(os.environ.get("PABEL_PLUGIN_DATA_DIR") or Path.home() / ".pabel") / "materialized"


def cache_dir(agent_id: str) -> Path:
    """Per-agent-product subdirectory - never shared across products
    installed side by side on the same machine (same reasoning as
    agent_session.py's own per-product credential keying): two different
    products' combined ABE keys can legitimately decrypt different sections
    of the same source document, so mixing their output in one namespace
    would be a correctness risk, not just tidiness."""
    return CACHE_ROOT / agent_id


async def create_async(source_path: str, name: str, agent_id: str) -> dict:
    """Read `source_path` through the normal relay (full re-verification of
    both the human session and this installation's credential, same as any
    other read) and write the result as a local JSON file under this agent
    product's own cache directory. Returns {"materialized_path": <str>,
    "result": <the read_document result dict>}."""
    result = await read_document_with_login_async(source_path, name, agent_id)
    directory = cache_dir(agent_id)
    directory.mkdir(parents=True, exist_ok=True)
    materialized_path = directory / f"{uuid.uuid4().hex}.json"
    materialized_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return {"materialized_path": str(materialized_path), "result": result}


def purge_all(agent_id: str) -> None:
    """Delete every materialized copy for this agent product - called
    unconditionally on SessionEnd, regardless of the termination reason.
    Best-effort: never raises on a missing or locked directory."""
    shutil.rmtree(cache_dir(agent_id), ignore_errors=True)
