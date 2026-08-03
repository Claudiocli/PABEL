# Coverage matrix

Original research 2026-07 (Claude Code and Sonnet 5's web search). **Every
"BUILT-TO-SPEC, UNVERIFIED" entry below was re-checked against current
official docs on 2026-08**, after a real first install attempt (VS Code)
found its path/schema guess was simply wrong - not tolerated, not close
enough, just never read by the vendor at all. That prompted re-verifying
every other adapter's assumptions the same way rather than trusting the
original research unverified. Real, confirmed bugs turned up in most of
them; see each section below and `docs/phase2-engineering-notes.md` for
the full account.

Every agent below has a genuinely different hook mechanism - not just a
different config file location, but different event granularity, different
blocking conventions, and different (or no) channel for handing substitute
content back to the model. This is why the connector needs one adapter per
agent rather than one shared wire format.

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

## VS Code native agent hooks (Preview) - UNVERIFIED, path/schema now CONFIRMED

- code.visualstudio.com/docs/agent-customization/hooks +
  code.visualstudio.com/docs/agents/reference/hooks-reference.
- **Two real bugs found and fixed 2026-08, live** (a real VS Code Copilot
  session was tried against the original guess and confirmed nothing fired
  at all - see phase2-engineering-notes.md and the "GitHub Copilot.md"
  transcript referenced there):
  1. Config location was guessed as `.vscode/hooks.json` - wrong. The real,
     confirmed workspace-scope location is **`.github/hooks/*.json`**. VS
     Code never reads `.vscode/hooks.json` at all - this wasn't a schema
     mismatch it tolerated, it was a file it never looked at.
  2. The JSON shape was written as Claude Code's nested
     `[{"hooks": [...]}]` wrapper - also wrong for a native `.github/hooks/`
     file. Confirmed native shape is a **flat** array directly under the
     event key: `{"hooks": {"PreToolUse": [{"type": "command",
     "command": ..., "timeout": ...}]}}`. VS Code is documented to parse
     but **not enforce** any `matcher` field at all (every hook always
     runs) - so there was never a reason to write one.
  3. Confirmed `hookSpecificOutput` shape is identical to Claude Code's
     (`hookEventName`, `permissionDecision`, `permissionDecisionReason`,
     `updatedInput`, `additionalContext`) - this part of the original
     research held up.
  4. **Real tool names are NOT Claude Code's** (`editFiles`/`createFile`/
     `deleteFile` for writes, `runTerminalCommand`/observed-in-the-wild
     `run_in_terminal` for shell) - `adapters/vscode.py` originally assumed
     `Write`/`Edit`/`Bash` and has been corrected. `editFiles`' input shape
     is `{"files": [...]}` (an array), not a single `file_path` string.
  5. Still unconfirmed: the exact tool name for a plain file *read* (no
     official source found naming one), and whether VS Code's MCP tool
     naming for `mcp_target` matches Claude Code's `mcp__<server>__<tool>`.
     The core `.abe`-mention detection doesn't depend on knowing the read
     tool's name, so this doesn't block the relay path specifically.
- Not yet tried against a real Copilot session with the corrected path -
  that's the next live test.

## GitHub Copilot CLI - UNVERIFIED, path now CONFIRMED

- docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/use-hooks.
- **Real bug found and fixed 2026-08**: config was written to a single
  `~/.copilot/hooks.json` file - wrong. User-level hooks actually load from
  `*.json` files inside a `~/.copilot/hooks/` *directory*
  (`%USERPROFILE%\.copilot\hooks\` on Windows); a single file directly
  under `~/.copilot/` was never read. Switched to the confirmed,
  project-scoped alternative instead - `.github/hooks/*.json` (same
  convention VS Code uses, documented to auto-convert this exact
  lowerCamelCase shape) - which also fits this package's own `--dir`-based
  install convention better than a global user-level file. Own filename
  (`pabel-copilot-cli.json`) so it never collides with vscode's
  `pabel.json` in the same directory.
- `preToolUse` hook, generic tool coverage (not restricted to Bash per
  docs) - `adapters/copilot_cli.py`'s exact tool-name assumptions
  (`Write`/`Edit`/`Bash`, copied from Claude Code) remain UNCONFIRMED;
  no source found this round naming Copilot CLI's actual tool identifiers.
- **Known vendor bug**: `additionalContext` is documented as valid for
  `preToolUse` but multiple open issues (`github/copilot-cli#2585`,
  `#2980`) confirm it is not reliably delivered into the agent's context
  today. `adapters/copilot_cli.py` therefore folds the relay's decrypted
  content into `permissionDecisionReason` itself (the one channel confirmed
  reliable) and sets `additionalContext` too, as a harmless duplicate that
  will start working for free if/when the vendor bug is fixed.

## Cursor - UNVERIFIED, response-shape bug found and fixed

- cursor.com/docs/hooks.
- Hooks introduced v1.7 (currently beta), three separate hook points:
  `beforeReadFile`, `beforeShellExecution`, `beforeMCPExecution`.
- Config confirmed: `hooks.json` at project (`.cursor/hooks.json`) or user
  (`~/.cursor/hooks.json`) level. Hook entries don't require `"type":
  "command"` (defaults to it) - a harmless extra field either way.
- **Real bug found and fixed 2026-08**: response shape was written as
  camelCase (`agentMessage`/`userMessage`) - wrong. Confirmed field names
  are **snake_case**: `{permission: "allow"|"deny"|"ask", agent_message,
  user_message}`. Same class of mistake as vscode's path guess - plausible,
  never caught because no live Cursor session had been tried either.
- `beforeMCPExecution`'s input field is confirmed as `tool_input` (not
  `arguments`, which the original code checked first) - swapped priority,
  `arguments` kept only as a defensive fallback.
- **Accepted gap**: no pre-write-block hook exists (only the post-hoc
  `afterFileEdit`) - low-impact here since this project has no legitimate
  `.abe` write path anyway (`core/decide.py`'s `DENY_MUTATING` already
  denies writes at the shared-core level regardless of which hook fired).
- Still no confirmed "MCP server name" field in `beforeMCPExecution`'s
  payload (only `tool_name`, `tool_input`, and either `url` or `command`
  identifying the server's own launch) - `adapters/cursor.py` still infers
  `mcp_target` heuristically from known tool names, a real fragility
  flagged in that module's docstring.

## Windsurf/Cascade - DEGRADED (was UNVERIFIED) - relay confirmed impossible

- docs.windsurf.com/windsurf/cascade/hooks (redirects to
  docs.devin.ai/desktop/cascade/hooks).
- Four pre-hooks: `pre_read_code`, `pre_write_code`, `pre_run_command`,
  `pre_mcp_tool_use`. Config confirmed at `.windsurf/hooks.json`
  (workspace-level).
- **The single most important open question from the original research is
  now settled, negatively**: blocking is confirmed **exit code 2 + stderr
  only, with no structured JSON response mechanism at all**, and stderr is
  explicitly documented as reaching a **human-visible log in the Cascade
  UI**, never the model's own context. There is no way for this adapter to
  transparently relay decrypted content back to the *model* - a real
  vendor ceiling, not something a future live test could still lift.
  Reclassified DEGRADED (same category as Codex CLI's Bash-only limit)
  rather than a plain UNVERIFIED a live test could fully clear.
- **Real bugs found and fixed 2026-08** in the per-hook input schema (now
  confirmed, a common envelope plus a per-hook `tool_info` object):
  `pre_run_command`'s field is `tool_info.command_line`, not `command`;
  `pre_mcp_tool_use`'s fields are `tool_info.mcp_server_name`/
  `mcp_tool_name`/`mcp_tool_arguments` - and critically, `mcp_server_name`
  is an **explicit, confirmed field**, so `mcp_target` here is now matched
  directly against it instead of the tool-name heuristic Cursor still
  needs. `pre_write_code`'s confirmed shape has no plain `content` field
  (an `edits` array of `{old_string, new_string}` instead) - low-impact
  here since `DENY_MUTATING` only needs `write_target` (`file_path`,
  already correct), not the content.
- Still unconfirmed: whether this fires as expected against a real Cascade
  session, and whether Windows specifically needs the command under this
  schema's documented `"powershell"` per-hook field instead of (or as well
  as) `"command"` - this package writes only `"command"` today.

## Gemini CLI - UNVERIFIED (held up well against the re-check)

- geminicli.com/docs/hooks/reference/.
- `BeforeTool` hook; `matcher` is a regex over the tool name, so `"*"`
  catches every tool (confirmed). Config confirmed at
  `.gemini/settings.json`, nested `{"matcher", "hooks": [{"name", "type":
  "command", "command", "timeout"}]}` shape (confirmed exact match).
- Blocks via `{"decision": "deny", "reason": ...}` (or exit code 2); reason
  text is "sent to the agent as a tool error" (confirmed).
- Confirmed: `additionalContext` in `hookSpecificOutput` belongs to
  *other* events (`AfterTool`, `BeforeAgent`), not `BeforeTool` at all -
  so, like Copilot CLI, folding the relay's content into `reason` instead
  (already how `adapters/gemini_cli.py` was written) is confirmed correct,
  not just a guess.
- MCP tools named `mcp_<server>_<tool>` (single underscore, confirmed) -
  recovered assuming the server name itself has no underscore (true for
  "pabel").
- This adapter's original research held up essentially unchanged under the
  2026-08 re-check - included here for completeness, not because anything
  needed fixing.

## OpenAI Codex CLI - NO ADAPTER (documented gap, moved from DEGRADED)

- **Reclassified 2026-08**: previously shipped as DEGRADED (Bash-only
  coverage). Re-checking current docs/issue trackers found something the
  original research missed - Codex CLI's hooks feature is explicitly
  documented as **"experimental (disabled by default, not available on
  Windows)"**. That is the exact same blocking criterion already applied
  to Cline below: employee machines can't be assumed non-Windows, so a
  hook surface that doesn't run there at all isn't a workable adapter,
  regardless of how good its (already Bash-only) coverage would be on the
  platforms where it does load.
- What would still apply if this is ever revisited on a non-Windows
  assumption: hooks are opt-in (`[features] hooks = true` in
  `~/.codex/config.toml`); `PreToolUse` only fires for the **Bash** tool
  (Read/Write/Edit/Apply Patch/web fetch/MCP calls never reach a hook);
  decision is deny-only (allow/ask/`updatedInput` parsed but ignored, no
  `additionalContext`).
- See `docs/known-gaps.md`.

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
