"""The Strategy interface every per-agent adapter implements. An adapter's
whole job is translating between one agent's own hook wire format and the
agent-agnostic core.types/core.decide - it must contain no detection or
relay logic of its own (that all lives in core/, shared by everyone).

Adapters are plain modules exposing a module-level `name` string plus
`parse`/`render` functions matching this Protocol's shape, rather than
classes - there's exactly one adapter instance needed per registry entry,
so a class hierarchy would add nothing a module doesn't already give for
free.
"""

import json
from typing import List, Protocol

from ..core.types import Decision, NormalizedCall, RenderedResponse


class Adapter(Protocol):
    name: str

    def parse(self, argv: List[str], stdin_bytes: bytes) -> NormalizedCall:
        """Read this agent's own hook payload (argv + stdin) and produce a
        NormalizedCall core.decide can reason about."""
        ...

    def render(self, decision: Decision) -> RenderedResponse:
        """Turn a Decision back into this agent's own expected response
        shape (JSON on stdout, or an exit code + stderr reason, etc.)."""
        ...


def fold_content_into_reason(decision: Decision) -> str:
    """decision.reason, with decision.content (if present) appended as
    readable text - the shared fallback body for copilot_cli/gemini_cli/
    windsurf (whose vendor doesn't reliably deliver, or doesn't have at
    all, a separate additionalContext-style channel for a blocked
    PreToolUse call) and cursor (whose agent_message IS its only channel,
    same shape). See each adapter module's own docstring for which case
    applies to it."""
    if decision.content is None:
        return decision.reason
    return f"{decision.reason}\n\nread_document result:\n{json.dumps(decision.content)}"
