# pabel - Claude Code plugin

Lets an agent work with PABEL CP-ABE-encrypted (`.abe`) documents without
ever handling their raw ciphertext itself. Every direct interaction with an
`.abe` file or a `documents/` folder - through **any** tool (Read, Grep,
Bash, Write, Edit, Glob, NotebookEdit, ...) - is blocked by a `PreToolUse`
hook. Where the hook can identify one concrete existing file, it relays
that file to an already-deployed PABEL server itself (read the bytes,
base64-encode, call `read_document` over `streamable-http`) and hands the
real, already access-controlled result back to the model as context. The
model never constructs a tool call containing ciphertext.

This plugin assumes a PABEL server (see the main project's `server/` and
`docs/phase2-engineering-notes.md`) is **already deployed and reachable**
- it ships no server code, no OpenABE binaries, no Postgres/Keycloak. If
you're setting up the server itself, do that first.

**This plugin is Claude Code's install path for a capability that now
also exists for other AI coding agents.** All detection/relay/policy
logic lives in the agent-agnostic `PABEL/connector/` package
(`pabel-connector`) - `hooks/pabel_relay_hook.py` here is a ~15-line
dispatch into it, not a separate implementation. If you're setting this
up for an agent other than Claude Code (Cursor, Windsurf, VS Code, GitHub
Copilot CLI, Gemini CLI, or a Bash-only degraded fit for OpenAI Codex
CLI), see `connector/README.md` and `connector/docs/coverage-matrix.md`
instead - only this Claude Code path has been verified end-to-end so far.

## Install

```
/plugin marketplace add <path-or-git-url-to-claude-plugin>
/plugin install pabel@pabel-marketplace
```

## Configure (per installation)

Add to your project's `.claude/settings.local.json` (not committed - these
are this deployment's own values):

```json
{
  "env": {
    "PABEL_SERVER_URL": "http://your-pabel-host:8001/mcp",
    "PABEL_KEYCLOAK_URL": "http://your-keycloak-host:8080",
    "PABEL_KEYCLOAK_REALM": "pabel",
    "PABEL_KEYCLOAK_CLIENT_ID": "pabel"
  }
}
```

- `PABEL_SERVER_URL` - the deployed `mcp-server-<agent>` container's
  streamable-http URL (used both by `.mcp.json`'s `whoami`/`read_document`
  registration and by the relay hook).
- `PABEL_KEYCLOAK_*` - used only by the relay hook's own login (below);
  must match the Keycloak realm/client the deployed server itself trusts.

Install the hook's own Python dependencies once (the hook runs as a plain
subprocess, outside whatever environment runs Claude Code itself):

```
pip install -r <plugin-install-path>/requirements.txt
```

## Log in (one-time, then as needed)

```
python <plugin-install-path>/login.py
```

Opens your system browser at Keycloak's hosted login page (MFA included,
whatever the realm requires) and saves a session the relay hook reuses and
refreshes automatically. `python login.py --logout` clears it.

**Note - two separate logins can exist**: this login is used only by the
relay hook (the mechanism that substitutes real content for a blocked
direct file read). Claude Code's own MCP client separately handles
authentication for this plugin's `.mcp.json`-registered tools (`whoami`,
and `read_document` if the model ever calls it directly with content it
already has) - it will prompt you to sign in via its own `/mcp` panel the
first time one of those is called, independent of the login above. Both
ultimately present a token the same deployed server verifies identically,
so this isn't a security gap - just an extra, currently unavoidable,
one-time prompt.

## Known open items

- The hook's tool-name matcher is intentionally unrestricted (fires for
  every tool, not an enumerated list) - verified directly against Read,
  Grep, Bash, Write, Edit, and Glob. Whether it also covers tool calls made
  from *within* a spawned subagent (the `Agent` tool) has not been
  verified - avoid spawning subagents for `.abe`-adjacent work until this
  is confirmed.
- Detecting "one concrete file" inside a free-text `Bash` command is
  best-effort (handles a bare path or one quoted with `"`/`'`, including
  spaces) - anything more complex (pipelines, multiple candidate paths) is
  denied outright rather than guessed at.
