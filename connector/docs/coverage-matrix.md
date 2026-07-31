# Coverage matrix

Research current as of 2026-07 (Claude Code and Sonnet 5's web search). Every
agent below has a genuinely different hook mechanism - not just a different
config file location, but different event granularity, different blocking
conventions, and different (or no) channel for handing substitute content
back to the model. This is why the connector needs one adapter per agent
rather than one shared wire format.

**Verification status is tracked in each adapter/installer module's own
docstring and in `registry.py`/`installers/registry.py` - this file is the
narrative version with sources, not the source of truth for what's actually
verified.**

## Claude Code - VERIFIED

- `PreToolUse` hook, no tool-name matcher restriction fires for every tool.
- Blocks via `hookSpecificOutput.permissionDecision: "deny"`.
- `additionalContext` confirmed delivered into the model's own context, not
  just a UI message - this is the mechanism the whole connector is built
  around.
- Confirmed end-to-end against a real deployed server this session (see
  `docs/phase2-engineering-notes.md` sec 9) before this refactor, and
  re-confirmed behavior-identical afterward.

## VS Code native agent hooks (Preview) - UNVERIFIED

- code.visualstudio.com/docs/agents/reference/hooks-reference.
- Identical JSON schema to Claude Code: `PreToolUse`,
  `hookSpecificOutput: {hookEventName, permissionDecision,
  permissionDecisionReason, updatedInput, additionalContext}`.
- VS Code is documented to auto-convert GitHub Copilot CLI's lowerCamelCase
  hook config (`preToolUse`) into this same PascalCase shape - strong signal
  the underlying data model is shared across Microsoft's own surfaces.
- Not testable this session (no paid Copilot subscription available).
- Exact hook-config file location was not pinned down by docs found this
  session - `installers/vscode.py` uses `.vscode/hooks.json` as a documented
  best guess.

## GitHub Copilot CLI - UNVERIFIED

- docs.github.com/en/copilot/reference/hooks-reference.
- `preToolUse` hook, generic tool coverage (not restricted to Bash).
- Blocks via `permissionDecision`/`permissionDecisionReason`; fail-closed on
  crash/non-zero exit, fail-open on timeout.
- **Known vendor bug**: `additionalContext` is documented as valid for
  `preToolUse` but multiple open issues (`github/copilot-cli#2585`,
  `#2980`) confirm it is not reliably delivered into the agent's context
  today. `adapters/copilot_cli.py` therefore folds the relay's decrypted
  content into `permissionDecisionReason` itself (the one channel confirmed
  reliable) and sets `additionalContext` too, as a harmless duplicate that
  will start working for free if/when the vendor bug is fixed.

## Cursor - UNVERIFIED

- Hooks introduced v1.7 (currently beta).
- Three separate hook points instead of one generic event:
  `beforeReadFile`, `beforeShellExecution`, `beforeMCPExecution`.
- Config confirmed: `hooks.json` at project (`.cursor/hooks.json`) or user
  (`~/.cursor/hooks.json`) level.
- Response shape confirmed: `{permission: "allow"|"deny"|"ask",
  agentMessage, userMessage}` - `agentMessage` is the channel that reaches
  the model.
- **Accepted gap**: no pre-write-block hook exists (only the post-hoc
  `afterFileEdit`) - low-impact here since this project has no legitimate
  `.abe` write path anyway (`core/decide.py`'s `DENY_MUTATING` already
  denies writes at the shared-core level regardless of which hook fired).
- No confirmed "MCP server name" field in `beforeMCPExecution`'s payload -
  `adapters/cursor.py` infers `mcp_target` heuristically from known tool
  names (`whoami`/`read_document`), a real fragility flagged in that
  module's docstring.

## Windsurf/Cascade - UNVERIFIED (least-confirmed adapter)

- docs.windsurf.com/windsurf/cascade/hooks.
- Four pre-hooks: `pre_read_code`, `pre_write_code`, `pre_run_command`,
  `pre_mcp_tool_use`.
- Blocking confirmed as **exit code 2**, with **stderr** as the reason -
  fundamentally different from every JSON-on-stdout adapter in this
  package.
- Config confirmed at `.windsurf/hooks.json` (workspace-level; Windsurf is
  documented to only load workspace-level hooks from this exact path).
- **Unconfirmed**: whether Windsurf delivers stderr text back into the
  model's own context at all, versus only into a human-visible log. This is
  the single most important thing to check before trusting this adapter -
  if there's no such channel, this degrades to "deny only, no transparent
  relay" for Windsurf specifically.
- Exact per-hook input JSON schema for these four hook names specifically
  was not found in public docs this session; `adapters/windsurf.py` parses
  defensively (several candidate key names, including a nested `tool_info`
  object seen in a *different*, confirmed Windsurf hook's payload).

## Gemini CLI - UNVERIFIED

- geminicli.com/docs/hooks/reference/.
- `BeforeTool` hook; `matcher` is a regex over the tool name, so `"*"`
  catches every tool (confirmed).
- Blocks via `{"decision": "deny", "reason": ...}` (or exit code 2); reason
  text is "sent to the agent as a tool error."
- Gemini CLI does have a richer context-injection hook (`BeforeAgent`,
  returning `hookSpecificOutput.additionalContext`), but it's a *different*,
  turn-scoped event, not wired to a specific blocked tool call - so, like
  Copilot CLI, the relay's content is folded into `reason` instead.
- MCP tools named `mcp_<server>_<tool>` (single underscore) - recovered
  assuming the server name itself has no underscore (true for "pabel").

## OpenAI Codex CLI - DEGRADED, UNVERIFIED

- Hooks are opt-in: `[features] hooks = true` in `~/.codex/config.toml`
  (marked "under development" by the vendor).
- **Major, vendor-acknowledged coverage gap**: `PreToolUse` only fires for
  the **Bash** tool. Read/Write/Edit/Apply Patch/web fetch/MCP tool calls
  never reach a hook at all - a native Codex file-read on an `.abe` file
  cannot be intercepted with today's hook surface.
- Decision is deny-only: allow/ask/`updatedInput` are parsed but ignored;
  there is no `additionalContext` support.
- This is why `codex_cli` is labeled DEGRADED everywhere rather than folded
  in as if it were equivalent coverage - see `docs/known-gaps.md`.

## Cline - NO ADAPTER (documented gap)

- Hooks (`beforeTool`/`afterTool`, v3.36+) are a JS/TS plugin SDK loaded
  into Cline's own runtime, not a simple external-command JSON config like
  every other agent here.
- **Hooks are explicitly macOS/Linux-only today - no Windows support at
  all.** Since employee machines can't be assumed non-Windows, this is out
  of scope this phase. See `docs/known-gaps.md`.

## Continue.dev - NO ADAPTER (documented gap)

- No pre-tool-use hook primitive exists at all - only a static
  allow/ask/disable `ToolPolicy` (IDE extension) and a CLI
  `permissions.yaml`.
- No mechanism to transparently substitute a relayed result for a blocked
  call. Its passive MCP registration (`whoami`/`read_document` as normal
  callable tools) still works, same as for any MCP-compatible agent,
  hook or no hook. See `docs/known-gaps.md`.
