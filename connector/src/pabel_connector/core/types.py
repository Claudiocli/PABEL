"""Agent-agnostic types shared by every adapter and the core policy engine
(decide.py). An adapter's whole job is translating its own agent's wire
format into a NormalizedCall, and a Decision back into that agent's own
response shape - nothing in this module, or in decide.py, knows any
agent's name.
"""

from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Optional, Tuple


@dataclass(frozen=True)
class NormalizedCall:
    """One tool-call attempt, in a shape decide() can reason about
    regardless of which agent produced it."""
    tool_name: str
    tool_input: Any
    is_write: bool = False
    is_execute: bool = False
    mcp_target: Optional[Tuple[str, str]] = None  # (server, tool), if this call targets an MCP tool
    write_target: Optional[str] = None  # path actually being written to, if is_write - deliberately
    # NOT inferred from scanning all of tool_input, so writing prose that merely discusses a
    # protected path (e.g. documentation) is never confused with writing to one


class DecisionKind(Enum):
    ALLOW = auto()
    DENY_OABE_BINARY = auto()
    DENY_MUTATING = auto()
    DENY_AMBIGUOUS = auto()
    DENY_WITH_RELAY = auto()
    DENY_AUTH_ERROR = auto()
    DENY_RELAY_ERROR = auto()
    DENY_CREDENTIAL_ACCESS = auto()
    DENY_HOOK_BYPASS = auto()


@dataclass(frozen=True)
class Decision:
    kind: DecisionKind
    reason: Optional[str] = None
    content: Optional[dict] = None  # read_document's structured result - only set on DENY_WITH_RELAY
    updated_input: Optional[dict] = None  # ALLOW only: tool_input with an agent_token injected -
    # a direct model call to pabel's own whoami/read_document needs this argument, which the
    # model itself must never see or supply; an adapter that can rewrite the call's input before
    # allowing it through (Claude Code's updatedInput) should. One that can't should ignore this
    # field and allow the call unmodified - the server then rejects the missing/empty agent_token
    # with a clean AuthError, a safe (if less convenient) fallback, never a security hole.


@dataclass(frozen=True)
class RenderedResponse:
    """What an adapter's render() produces. hook.py writes stdout/stderr
    and exits with exit_code - the three fields together cover every
    agent's blocking convention seen so far (JSON-on-stdout-exit-0, or
    exit-code-2-with-stderr-reason)."""
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
