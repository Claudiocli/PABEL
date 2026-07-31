"""OpenAI Codex CLI's `PreToolUse` hook.

STATUS: DEGRADED, UNVERIFIED - this is a real, vendor-acknowledged coverage
gap, not a bug in this design (see connector/docs/coverage-matrix.md and
connector/docs/known-gaps.md). As of this writing, Codex CLI's
`PreToolUse` **only fires for the Bash tool** - Read, Write, Edit, Apply
Patch, web fetch, and MCP tool calls never reach a hook at all, so a
native Codex file-read tool call on an `.abe` file cannot be intercepted
with today's hook surface. This adapter can therefore only ever catch
`.abe` access attempted *through* Bash (`cat`, `oabe_dec`, etc.) - ship it
labeled as partial coverage everywhere (README's coverage table,
known-gaps.md), never as equivalent to the other adapters.

Further vendor limitations that shape this adapter: hooks are opt-in
(`[features] hooks = true` in `~/.codex/config.toml` - the installer must
set this, not just drop a hook config file) and the only decision Codex
acts on is "deny" - allow/ask/updatedInput are parsed but ignored, and
there is no additionalContext support at all. The relay's decrypted
content therefore has nowhere reliable to go except folded into
`permissionDecisionReason` itself, same fallback used for Copilot CLI/
Gemini CLI, on the assumption (unconfirmed) that a denied Bash call's
reason text is at least visible to the model as tool-error output.
"""

import json

from ..core.types import Decision, DecisionKind, NormalizedCall, RenderedResponse

name = "codex-cli"


def parse(argv, stdin_bytes) -> NormalizedCall:
    payload = json.loads(stdin_bytes.decode("utf-8") or "{}")
    tool_input = payload.get("tool_input") or {}
    # tool_name is always "Bash" for Codex CLI's PreToolUse today - see
    # module docstring.
    return NormalizedCall(tool_name="Bash", tool_input=tool_input, is_execute=True)


def render(decision: Decision) -> RenderedResponse:
    if decision.kind == DecisionKind.ALLOW:
        return RenderedResponse()

    reason = decision.reason
    if decision.content is not None:
        reason = f"{reason}\n\nread_document result:\n{json.dumps(decision.content)}"

    return RenderedResponse(stdout=json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
