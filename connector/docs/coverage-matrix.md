# Coverage matrix

The current state of every agent this package targets, as of 2026-08-03.
For the *history* of how each row got here (bugs found, live tests, dated
narrative) see `docs/phase2-engineering-notes.md` - this file is the
current-state reference, not the story.

## Legend

- **Status** - `VERIFIED` (confirmed end-to-end against a real install),
  `UNVERIFIED` (built to current vendor docs, never tried live),
  `DEGRADED` (a real vendor limitation caps it regardless of testing),
  `MCP-ONLY` (MCP tool registration exists, but no hook/interception
  mechanism is confirmed to exist for this product at all - zero
  enforcement, see `docs/known-gaps.md`), `NO ADAPTER` (documented gap,
  see `docs/known-gaps.md`).
- **Blocking channel** - how a denied call is communicated back: a
  structured JSON field the model reads, or an OS-level signal (exit
  code) with a human-only log.
- **Content channel** - where the relayed, decrypted document text
  actually lands: reliably in the model's context, folded into the deny
  reason as a fallback, or nowhere the model itself ever sees (human log
  only).
- **`--global`** - whether `pabel-connector install <agent> --global`
  writes to a *confirmed* user-level location instead of a project
  directory (see `installers/base.py:global_config_path()`). Never
  guessed - an agent without one is rejected with an error, not a wrong
  path.
- **Direct MCP tools** - whether `pabel_connector/mcp_local_server.py`
  (whoami/read_document/login, callable by the model directly, independent
  of the hook) is wired into that agent's own installer yet.

## Matrix

| Agent | Status | Hook config (project) | `--global` | Blocking channel | Content channel | Direct MCP tools | Live-verified |
|---|---|---|---|---|---|---|---|
| **Claude Code** | VERIFIED | `.claude/settings.json` (nested) | `~/.claude/settings.json` | `hookSpecificOutput.permissionDecision` | `additionalContext` (confirmed delivered) | Yes (`.mcp.json`) | **Yes** - full session, read/write/Bash/grep, real deployed server |
| **VS Code (Copilot, native hooks)** | **VERIFIED** | `.github/hooks/pabel.json` | Not supported (no confirmed location) | `hookSpecificOutput.permissionDecision` | `additionalContext` | Yes (`.vscode/mcp.json`) | **Yes** (2026-08-03) - blocked read, auto-login, relay, correct per-user `[ACCESS DENIED]` |
| **GitHub Copilot CLI** | UNVERIFIED | `.github/hooks/pabel-copilot-cli.json` | `~/.copilot/hooks/pabel-copilot-cli.json` | `hookSpecificOutput.permissionDecision` | Folded into `permissionDecisionReason` (vendor bug: `additionalContext` unreliable, #2585/#2980) | Not wired yet | No |
| **Cursor** | UNVERIFIED | `.cursor/hooks.json` | `~/.cursor/hooks.json` | `{"permission": "allow"\|"deny"\|"ask"}` | `agent_message` (its only channel) | Not wired yet | No |
| **Windsurf/Cascade** | DEGRADED | `.windsurf/hooks.json` | `~/.codeium/windsurf/hooks.json` (different shape - not `~/.windsurf/`) | Exit code 2 + stderr (no JSON channel at all) | stderr → human-visible Cascade log **only** - confirmed never reaches the model | Not wired yet | No |
| **OpenAI Codex CLI** | MCP-ONLY | `~/.codex/config.toml` (shared with ChatGPT desktop, no project-scoped variant) | `~/.codex/config.toml` (global only) | - (no hook exists) | - (no hook exists) | Yes (no hook underneath) | No |
| **ChatGPT desktop app** | MCP-ONLY | `~/.codex/config.toml` (same file as Codex CLI above) | `~/.codex/config.toml` (global only) | - (no hook exists) | - (no hook exists) | Yes (no hook underneath) | No |
| **Cline** | NO ADAPTER | - | - | - | - | - | - |
| **Continue.dev** | NO ADAPTER | - | - | - | - | - | - |

## Per-agent open questions

Only what doesn't fit a cell above.

- **Claude Code** - none open; this is the reference implementation.
- **VS Code** - exact tool name for a plain file *read* still unconfirmed
  (no official source names one); whether `mcp_target`'s naming matches
  Claude Code's `mcp__<server>__<tool>` exactly is unconfirmed. Neither
  blocks the relay path, which is confirmed working regardless.
- **GitHub Copilot CLI** - tool-name assumptions (`Write`/`Edit`/`Bash`)
  are borrowed from Claude Code, unconfirmed for this product specifically.
- **Cursor** - no pre-write-block hook exists (accepted: `core/decide.py`'s
  `DENY_MUTATING` already covers writes at the shared-core level regardless
  of which hook fired); `mcp_target` is inferred heuristically from known
  tool names, since no explicit MCP-server-name field was found in its
  `beforeMCPExecution` payload - a real fragility if another MCP server
  ever exposes a same-named tool.
- **Windsurf** - unconfirmed whether Windows specifically needs the
  command under a documented `"powershell"` per-hook field instead of (or
  as well as) `"command"` - this package writes only `"command"` today.
  The content-channel ceiling itself is not an open question - it's
  confirmed, permanent, and DEGRADED reflects that.
- **Codex CLI / ChatGPT desktop app** - confirmed (not assumed) via
  OpenAI's own docs that both share one `~/.codex/config.toml`; installers
  register distinct per-product MCP server names to avoid the two
  colliding in that shared file (see `installers/codex_family.py`).
  Neither product has any confirmed hook/tool-interception mechanism at
  all - only `default_tools_approval_mode`/`disabled_tools`, which control
  whether a tool prompts or is blocked outright, never what content it
  returns. `whoami`/`read_document`/`materialize_document` are directly
  callable for both - there's just no enforcement, no blocking of a direct
  encrypted-file read.
- **Cline / Continue.dev** - see `docs/known-gaps.md` for why neither has
  any adapter, or any install action at all.
