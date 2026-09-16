"""Test-wide isolation of this machine's real PABEL state.

Both module-level stores resolve their path once at import, from the real
Path.home(). Any test reaching a code path that calls `store_credentials`
therefore writes to the developer's own ~/.pabel/agent_credentials.json -
which is exactly what happened on 2026-09-16: two installer tests passed
`--client-id x --client-secret y` and relied on the CLI rejecting the call
before it got that far. When `install` stopped rejecting it (machine-wide
became the default scope), those same tests silently overwrote two real
installations' credentials with "x"/"y".

This is not a substitute for a test stubbing what it means to stub; it is
the floor that stops a change in behaviour *elsewhere* from reaching real
files through a test that never intended to write one.
"""

import pytest

from pabel_connector.pabel_client import agent_session, session


@pytest.fixture(autouse=True)
def isolate_pabel_state(tmp_path_factory, monkeypatch):
    # tmp_path_factory, not tmp_path: several installer tests assert that a
    # run wrote nothing into tmp_path at all, and a state directory planted
    # inside it would break exactly the checks worth keeping.
    data_dir = tmp_path_factory.mktemp("pabel-state")
    monkeypatch.setattr(agent_session, "DATA_DIR", data_dir)
    monkeypatch.setattr(agent_session, "CREDENTIALS_FILE",
                        data_dir / "agent_credentials.json")
    monkeypatch.setattr(session, "DATA_DIR", data_dir)
    monkeypatch.setattr(session, "SESSION_FILE", data_dir / "session.json")
    return data_dir
