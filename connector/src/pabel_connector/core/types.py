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


class DecisionKind(Enum):
    ALLOW = auto()
    DENY_OABE_BINARY = auto()
    DENY_MUTATING = auto()
    DENY_AMBIGUOUS = auto()
    DENY_WITH_RELAY = auto()
    DENY_AUTH_ERROR = auto()
    DENY_RELAY_ERROR = auto()


@dataclass(frozen=True)
class Decision:
    kind: DecisionKind
    reason: Optional[str] = None
    content: Optional[dict] = None  # read_document's structured result - only set on DENY_WITH_RELAY


@dataclass(frozen=True)
class RenderedResponse:
    """What an adapter's render() produces. hook.py writes stdout/stderr
    and exits with exit_code - the three fields together cover every
    agent's blocking convention seen so far (JSON-on-stdout-exit-0, or
    exit-code-2-with-stderr-reason)."""
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
