"""pabel_client/materialize.py: one-shot local decrypted copies. Deliberately
NOT kept fresh after being written - see the module's own docstring for why
(no server-side document store, no always-on local process to receive a
push). These tests cover exactly what the module actually guarantees: a
fresh relay call at creation time, and unconditional purge on demand - never
a refresh-on-access behavior, since that was explicitly rejected in favor of
"once on disk, it's not our problem anymore" (docs/phase2-engineering-notes.md
19.3)."""

import json

import anyio
import pytest

import pabel_connector.pabel_client.materialize as materialize


def run(coro):
    return anyio.run(lambda: coro)


@pytest.fixture(autouse=True)
def isolated_cache_root(tmp_path, monkeypatch):
    monkeypatch.setattr(materialize, "CACHE_ROOT", tmp_path / "materialized")


def test_cache_dir_is_scoped_per_agent_product(tmp_path):
    # Never shared across products installed side by side on the same
    # machine - two products' combined ABE keys can legitimately decrypt
    # different sections of the same source document.
    assert materialize.cache_dir("claude-code") != materialize.cache_dir("cursor")
    assert materialize.cache_dir("claude-code").parent == materialize.CACHE_ROOT


def test_create_async_writes_the_relay_result_to_a_local_file(monkeypatch):
    calls = []

    async def fake_read_document_with_login_async(path, name, agent_id):
        calls.append((path, name, agent_id))
        return {"name": name, "sections": [{"name": "s1", "accessible": True, "text": "hello"}]}

    monkeypatch.setattr(materialize, "read_document_with_login_async",
                        fake_read_document_with_login_async)

    result = run(materialize.create_async("source.abe", "doc", "claude-code"))

    assert calls == [("source.abe", "doc", "claude-code")]
    assert result["result"]["sections"][0]["text"] == "hello"
    written = json.loads(open(result["materialized_path"], encoding="utf-8").read())
    assert written == result["result"]


def test_create_async_scopes_the_written_file_under_the_agent_products_own_dir(monkeypatch):
    async def fake(path, name, agent_id):
        return {"ok": True}

    monkeypatch.setattr(materialize, "read_document_with_login_async", fake)
    result = run(materialize.create_async("source.abe", "doc", "claude-code"))
    from pathlib import Path
    assert Path(result["materialized_path"]).parent == materialize.cache_dir("claude-code")


def test_create_async_calls_are_independent_files(monkeypatch):
    # No manifest, no source-path bookkeeping - each call is just a fresh,
    # independently named file (see the module's own docstring).
    async def fake(path, name, agent_id):
        return {"ok": True}

    monkeypatch.setattr(materialize, "read_document_with_login_async", fake)
    first = run(materialize.create_async("source.abe", "doc", "claude-code"))
    second = run(materialize.create_async("source.abe", "doc", "claude-code"))
    assert first["materialized_path"] != second["materialized_path"]


def test_purge_all_deletes_only_the_named_agent_products_directory(monkeypatch):
    async def fake(path, name, agent_id):
        return {"ok": True}

    monkeypatch.setattr(materialize, "read_document_with_login_async", fake)
    claude_result = run(materialize.create_async("source.abe", "doc", "claude-code"))
    cursor_result = run(materialize.create_async("source.abe", "doc", "cursor"))

    materialize.purge_all("claude-code")

    from pathlib import Path
    assert not Path(claude_result["materialized_path"]).exists()
    assert Path(cursor_result["materialized_path"]).exists()


def test_purge_all_is_a_no_op_on_a_never_created_directory():
    materialize.purge_all("never-installed-agent")  # must not raise
