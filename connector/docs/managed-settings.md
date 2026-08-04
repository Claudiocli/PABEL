# Managed settings: making the PABEL hook and MCP registration non-removable

`pabel-connector install claude-code --dir .` writes into a project's own
`.claude/settings.json` (or, with `--global`, `~/.claude/settings.json`) and
`.mcp.json` - all files a developer on that machine can freely edit or delete.
Nothing described anywhere else in this package stops an employee from removing
the hook entry, or the `pabel`/`pabel-connector` MCP server registrations,
themselves; enforcement today relies entirely on them not doing that. This
document is for whoever administers the organization's machines, not for the
employee running `install` - it's a separate, IT-side deployment step, the same
kind of split `server/agents_admin.py` already draws between "admin-only actions"
and "what an employee's own install command does." Two separate mechanisms are
involved - hooks and MCP server registration aren't governed by the same flag,
covered in their own sections below.

This is scoped to **Claude Code only**. Claude Code has a confirmed, real
mechanism for this (below). GitHub Copilot/VS Code has an analogous-sounding
"Copilot managed settings" channel (MDM-deployed, applies across VS Code and
Copilot CLI), but nothing found while researching this confirms it covers
*hooks* specifically, as opposed to feature flags like `chat.agent.enabled` or
model access - don't write deployment guidance for it from a guess. Revisit once
that's confirmed against current vendor docs, the same discipline this project
already applies everywhere else (see `docs/coverage-matrix.md`).

## What `allowManagedHooksOnly` actually does

Claude Code supports an enterprise-managed settings layer that sits above every
other settings source (user, project, local) and that CLI flags cannot relax.
Setting `allowManagedHooksOnly: true` in that layer means **only hooks defined in
the managed layer itself run at all** - a project's or user's own
`.claude/settings.json` can no longer add, remove, or override hook entries.
Pairing it with `allowManagedPermissionRulesOnly: true` closes the other half:
nobody can grant themselves permissions beyond what the managed layer allows
either. Together, that's a real hard perimeter, not a default a developer can
work around - **for hooks and permissions specifically**. Confirmed against
current docs: `allowManagedPermissionRulesOnly` does *not* also lock down MCP
server registration - that needs its own, separate mechanism, covered below.

## Deploying it on Windows

Two channels, both read the same JSON shape - pick whichever fits the
organization's existing device-management tooling:

**Registry (GPO/Intune)** - a string value under:
```
HKLM\SOFTWARE\Policies\ClaudeCode\Settings
```
containing the full managed-settings JSON as text. Deployed the same way any
other machine-wide policy is pushed via Group Policy or Intune.

**File-based**, for tooling that prefers dropping a file (Chef/Puppet/Ansible,
a plain deployment script, etc.):
```
C:\Program Files\ClaudeCode\managed-settings.json
```

## The JSON to deploy

Pin exactly the hook commands `pabel-connector install claude-code` itself
would have written - same values `installers/claude_code.py` produces via
`base.hook_command()`, just placed in the managed layer instead of a
project/user file:

```json
{
  "allowManagedHooksOnly": true,
  "allowManagedPermissionRulesOnly": true,
  "hooks": {
    "PreToolUse": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "\"<path-to-python>\" -m pabel_connector.hook claude-code",
            "timeout": 200
          }
        ]
      }
    ],
    "SessionEnd": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "\"<path-to-python>\" -m pabel_connector.hook claude-code:session-end",
            "timeout": 200
          }
        ]
      }
    ]
  }
}
```

`<path-to-python>` needs to be a Python interpreter with `pabel-connector`
installed, reachable identically on every managed machine - a per-machine venv
path won't do for a fleet-wide policy; a machine-wide Python install (or a
wrapper script resolved via PATH) is the practical choice here, unlike the
per-user venv path `pabel-connector install` resolves for a single developer's
own machine (`base.hook_command()`'s docstring explains why it hardcodes
`sys.executable` for that single-machine case - a fleet deployment needs its own
equivalent, fixed path instead).

## Locking the MCP registration too (`managed-mcp.json`)

`allowManagedHooksOnly` only covers hooks - it does nothing for `.mcp.json`'s
`pabel`/`pabel-connector` server entries, which a developer can still edit or
remove even with the hook layer fully locked. MCP server enforcement is a
**separate file and mechanism**, confirmed against Claude Code's own current
docs (`code.claude.com/docs/en/managed-mcp`) rather than assumed to work the
same way as `allowManagedHooksOnly` - it doesn't.

Two ways to enforce this, and they are not equally safe:

- **`managed-mcp.json` in exclusive-control mode** (recommended): deploy this
  file and Claude Code loads *only* the servers it defines - a user can't add,
  modify, or use any other MCP server, plugin-provided ones included.
  `claude mcp add` fails outright with an explicit enterprise-policy error
  rather than silently succeeding.
- **`allowedMcpServers` + `allowManagedMcpServersOnly: true`** in
  `managed-settings.json` - a softer allowlist mechanism with a real bypass:
  `claude --mcp-config ./unapproved.mcp.json --allowedTools "mcp__x__*"` loads
  an unapproved server anyway, tools and all - the documented allowlist is
  silently not enforced (`anthropics/claude-code#31508`, reproduced on
  v2.1.70). **This is not a pending fix** - the issue is closed *"not
  planned"*, i.e. this is accepted current behavior for the allowlist
  mechanism specifically, not a bug queued for a patch. `managed-mcp.json`'s
  exclusive control mode is confirmed **not** affected (it correctly blocks
  `--mcp-config` from adding anything). Don't rely on the allowlist mechanism
  for something as security-relevant as this - use `managed-mcp.json`.

`managed-mcp.json` is a standalone file - unlike `managed-settings.json`, it
**cannot** be delivered through server-managed settings (the Claude.ai admin
console channel), only by something with administrator write access to a
system path, same deployment story as before (GPO/Intune/fleet management).
Windows path:
```
C:\Program Files\ClaudeCode\managed-mcp.json
```

Content - same shape as a normal `.mcp.json`, pinning exactly the two servers
`installers/claude_code.py` itself would have registered:

```json
{
  "mcpServers": {
    "pabel": {
      "type": "http",
      "url": "${PABEL_SERVER_URL}"
    },
    "pabel-connector": {
      "type": "stdio",
      "command": "<path-to-python>",
      "args": ["-m", "pabel_connector.mcp_local_server", "claude-code"]
    }
  }
}
```

Same `<path-to-python>` caveat as the hook JSON above - a fleet-wide fixed
path, not a per-developer venv. `${PABEL_SERVER_URL}` still expands from each
user's own environment (`.mcp.json`'s existing convention, unchanged here) -
no PABEL secret ever needs to live in this file: `pabel`'s own auth is the
human's Keycloak session (handled by Claude Code's own MCP OAuth flow, not
embedded here), and `pabel-connector`'s command/args carry no credential
either - the per-installation `client_id`/`client_secret` stays exactly where
it already lives, `~/.pabel/agent_credentials.json`, read locally by the
process itself. This matters because **any user on the machine can read
`managed-mcp.json`** (it's not access-controlled like a settings file with
secrets would need to be) - if this project ever needed to put a real secret
in an MCP server definition, this file would be the wrong place for it (see
the docs' own guidance on `${VAR}` expansion / OAuth / `headersHelper` for
that case); it doesn't arise here today.

Validate the same way as hooks, plus a `.mcp.json`-specific check:
```
claude mcp list
```
must show only `pabel`/`pabel-connector` on a managed machine. Attempting
`claude mcp add` should fail with `Cannot add MCP server: enterprise MCP
configuration is active and has exclusive control over MCP servers` - if it
succeeds instead, the file isn't being read (wrong path, or a permissions
issue), the same silent-failure risk as a malformed `managed-settings.json`.

## The one failure mode that matters most

If `managed-settings.json` (or the registry value) contains invalid JSON, Claude
Code **silently ignores the entire managed layer** and falls back to whatever
the user's/project's own settings say - which, if this is the *only* thing
enforcing PABEL, means enforcement quietly disappears with no error anywhere.
This is explicitly the worst failure mode: it looks like everything is still
working. After every deployment or change, verify from an actual managed
machine:

```
/status
```

inside Claude Code, and confirm the "Setting sources" line shows the managed
layer active (e.g. `(HKLM)` or `(file)`), not just user/project sources.

## VS Code - not yet actionable

"Copilot managed settings" (deployed via Intune/Jamf/Group Policy, registry key
`HKEY_LOCAL_MACHINE\SOFTWARE\Policies\GitHubCopilot` on Windows) is confirmed to
exist and to override user-configured settings on managed devices. What isn't
confirmed is whether it can lock down *hooks* specifically the way
`allowManagedHooksOnly` does for Claude Code, or only broader feature toggles.
Don't deploy anything for VS Code based on this document until that's checked
against current GitHub/VS Code enterprise-policy docs - tracked as an open item,
not silently assumed to work the same way.
