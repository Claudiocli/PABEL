"""hook.py's main(): dispatch to either SESSION_END_HANDLERS (bypassing
decide()/ADAPTERS entirely - SessionEnd isn't a tool call) or the normal
ADAPTERS/decide() path. See registry.py for why these are two separate
tables rather than one."""

import io
import json

import pabel_connector.hook as hook


def _set_stdin(monkeypatch, data: bytes):
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(data)))


def test_main_requires_an_argument(capsys):
    assert hook.main(argv=[]) == 2
    assert "usage:" in capsys.readouterr().err


def test_main_reports_unknown_key(monkeypatch, capsys):
    _set_stdin(monkeypatch, b"{}")
    assert hook.main(argv=["not-a-real-agent"]) == 2
    assert "unknown agent key" in capsys.readouterr().err


def test_main_routes_session_end_keys_to_their_own_handler_not_decide(monkeypatch, capsys):
    # Must never reach core.decide.decide() or ADAPTERS - a SessionEnd
    # payload has no tool_name/tool_input, so forcing it through
    # NormalizedCall/parse() would be a bug, not a fallback.
    calls = []

    def fake_handler(stdin_bytes):
        calls.append(stdin_bytes)
        from pabel_connector.core.types import RenderedResponse
        return RenderedResponse(stdout="handled\n")

    monkeypatch.setitem(hook.SESSION_END_HANDLERS, "claude-code:session-end", fake_handler)

    def fail_if_decide_called(call, agent_id):
        raise AssertionError("decide() must not be called for a SessionEnd key")

    monkeypatch.setattr(hook, "decide", fail_if_decide_called)
    _set_stdin(monkeypatch, json.dumps({"reason": "clear"}).encode())

    exit_code = hook.main(argv=["claude-code:session-end"])
    assert exit_code == 0
    assert calls == [json.dumps({"reason": "clear"}).encode()]
    assert capsys.readouterr().out == "handled\n"


def test_main_still_routes_ordinary_keys_through_decide(monkeypatch):
    from pabel_connector.core.types import Decision, DecisionKind

    _set_stdin(monkeypatch, json.dumps({"tool_name": "Read", "tool_input": {}}).encode())
    calls = []

    def fake_decide(call, agent_id):
        calls.append((call, agent_id))
        return Decision(DecisionKind.ALLOW)

    monkeypatch.setattr(hook, "decide", fake_decide)
    hook.main(argv=["claude-code"])
    assert len(calls) == 1
    assert calls[0][1] == "claude-code"
