# pabel-connector

Agent-agnostic enforcement for PABEL CP-ABE-gated documents: one shared
policy core (`core/`), a thin adapter per AI coding agent (`adapters/`), and
an installer CLI that wires the right one into whichever agents an employee
actually uses.

When a wired agent tries to touch an `.abe` file directly - read it, write
it, shell out to `cat`/`oabe_dec` - that call is blocked, and where exactly
one concrete file can be identified it's relayed to the deployed server's
`read_document` instead, handing the model the already-decrypted,
access-controlled result in the same turn.

Assumes a PABEL server is **already deployed and reachable** (see `server/`).
This package ships no server code, no OpenABE binaries, no Postgres/Keycloak.

## Coverage

| Agent | Status | `--global` |
|---|---|---|
| Claude Code | **VERIFIED** | Yes |
| VS Code (native agent hooks) | **VERIFIED** | No |
| Cursor | UNVERIFIED | Yes |
| GitHub Copilot CLI | UNVERIFIED | Yes |
| opencode | UNVERIFIED | Yes |
| Windsurf/Cascade | **DEGRADED** - can block, cannot deliver content to the model | Yes |
| Codex CLI | **MCP TOOLS ONLY** - zero enforcement | Required |
| ChatGPT desktop app | **MCP TOOLS ONLY** - zero enforcement | Required |
| Cline, Continue.dev | **NO ADAPTER** | N/A |

`docs/coverage-matrix.md` has the full picture - paths, blocking channels,
what's confirmed vs. assumed. **Read it before trusting anything beyond
Claude Code/VS Code in a real rollout**: "built to spec" is not "confirmed
against the real agent". `docs/known-gaps.md` explains every non-VERIFIED row.

## Install

```
pip install -e .                    # from a checkout
pipx install <wheel-or-git-url>     # see "Distribution"
```

On Windows, `pip install -e .` doesn't put `pabel-connector.exe` on `PATH`.
Either activate the venv (`.\.venv\Scripts\Activate.ps1`) or call it
directly (`.\.venv\Scripts\pabel-connector.exe ...`).

## Set up

**1. Environment** - required in the shell that runs `install`, and in
whatever environment later runs each agent:

```powershell
$env:PABEL_KEYCLOAK_URL       = "<your deployment's value>"
$env:PABEL_KEYCLOAK_REALM     = "<your deployment's value>"
$env:PABEL_KEYCLOAK_CLIENT_ID = "<your deployment's value>"
$env:PABEL_SERVER_URL         = "<your deployment's value>"
```

Codex CLI/ChatGPT desktop are the exception: `install` captures these into
their own `config.toml` entry, so they need no persistent env var at all.

**2. Get a credential per agent** from your admin - one per agent, never
shared between them (`server/agents_admin.py create-installation <agent>`).

**3. Install.** Omit the agent name to pick several from a list:

```
pabel-connector install                 # choose from a list, prompts per agent
pabel-connector install <agent> --client-id <id> --client-secret <secret>
```

Installs are **machine-wide by default**, so the agent is enforced from every
directory. This is a security default, not a convenience one: the credential
in `~/.pabel/` authenticates from anywhere, so enforcement confined to one
project stops applying as soon as the agent starts elsewhere - and absent
wiring fails open, silently.

`--dir <path>` confines an install to one project and has to be asked for.
`vscode` has no confirmed user-level location, so it is project-scoped and
`install` says so rather than looking machine-wide; `--global` is still
accepted everywhere else for explicitness, and rejected for `vscode` rather
than guessing a path. `doctor` reports an installation that only covers one
directory.

The multi-select flow prompts for each agent's own credential separately and
refuses `--client-id`/`--client-secret`, since reusing one pair across agents
would give them a shared identity - exactly what the server's `resolve_agent()`
and `DENY_CONFIG_TAMPER` exist to prevent.

**4. Log in and check:**

```
pabel-connector login
pabel-connector doctor    # env vars, login status, hook wiring
pabel-connector list      # every agent, its status, --global support
```

Login opens your system browser at Keycloak (MFA included) and saves a
session every relay call reuses and refreshes automatically.

### Optional: local audit log

`--audit-public-key <path>` turns on a local, encrypted,
**confidentiality-only** log of PABEL-relevant decisions, using a
company-wide keypair an admin generates once (`agents_admin.py
generate-audit-keypair`). Off by default. It is explicitly *not* an
integrity guarantee - see `docs/known-gaps.md`.

## Two credentials, not one

- `--client-id`/`--client-secret` establish **which agent installation** this
  is. Verified by the server on every relay call.
- `pabel-connector login` establishes **which human** you are.

Both are combined into one ABE key server-side. Neither substitutes for the
other, and the agent never sees either one.

## Materialized copies (Claude Code only)

`materialize_document(path, name)` writes a decrypted document to a real
local file. **Once written, PABEL no longer governs that file** - no
re-verification, no write protection, no freshness tracking. A `SessionEnd`
hook deletes every materialized copy when the session ends, bounding how long
a decrypted copy sits on disk to one session.

## Distribution

Released as a wheel attached to a GitHub Release - `pip install <url-to-the-.whl>`
needs nothing else. An internal package index is deliberately out of scope.

## Further reading

| Document | What's in it |
|---|---|
| `docs/coverage-matrix.md` | Per-agent paths, channels, verification status |
| `docs/known-gaps.md` | Why each non-VERIFIED agent is where it is |
| `docs/verification-procedure.md` | Checklist to run before promoting a status |
| `docs/managed-settings.md` | Making the hook and MCP entries non-removable (Claude Code) |
| `../docs/phase2-engineering-notes.md` | Dated history: bugs found, decisions made |

**Known limitation**: nothing here stops an employee from deleting the hook
or the MCP entries from their own config after install. `docs/managed-settings.md`
covers the enterprise mechanisms that do, for Claude Code.
