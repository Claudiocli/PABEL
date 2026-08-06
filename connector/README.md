# pabel-connector

Agent-agnostic enforcement for PABEL CP-ABE-gated documents: one shared
policy core (`core/`), a thin adapter per AI coding agent (`adapters/`),
and an installer CLI that wires the right one into whichever agent(s) an
employee actually uses - Strategy pattern all the way down, so adding
support for a new agent means one new adapter/installer pair, never a
rewrite of the detection or relay logic.

This package assumes a PABEL server is **already deployed and reachable**
(see the main project's `server/` and `docs/phase2-engineering-notes.md`)
- it ships no server code, no OpenABE binaries, no Postgres/Keycloak. If
you're setting up the server itself, do that first; this is what an
employee installs on their own machine afterward.

## What this actually does

Every adapter's job is the same: when the agent it's wired into is about
to touch an `.abe` file directly (read, write, shell out to `cat`/
`oabe_dec`, etc.), block that specific call and, where exactly one
concrete file can be identified, relay it to the deployed server's
`read_document` tool instead - handing the model back the real,
already-decrypted-and-access-controlled result in the same turn. The model
never constructs a tool call containing raw ciphertext and never manually
assembles a base64 blob; the only sanctioned path is "the adapter/hook
sends it to the server."

## Coverage table

| Agent | Status | `--global`? | Direct MCP tools | Notes |
|---|---|---|---|---|
| Claude Code | **VERIFIED** | Yes | Yes | Confirmed end-to-end against a real deployed server. Installed exactly like every other agent - `pabel-connector install claude-code --dir .` - no special or separate path. |
| VS Code (native agent hooks, Preview) | **VERIFIED** | No (no confirmed user-level location) | Yes | Confirmed live 2026-08-03: blocked read, automatic browser+MFA login, relay, correct per-user `[ACCESS DENIED]` result. |
| GitHub Copilot CLI | UNVERIFIED | Yes | Not yet wired | Path confirmed against current docs; `additionalContext` unreliable per open vendor issues (#2585/#2980) - content folded into the deny reason instead. |
| Cursor | UNVERIFIED | Yes | Not yet wired | 3 hook points (read/shell/MCP); response fields confirmed snake_case. No pre-write-block hook exists (low-impact - `core/decide.py`'s `DENY_MUTATING` covers writes regardless). |
| Windsurf/Cascade | **DEGRADED** | Yes (different shape: `~/.codeium/windsurf/`, not `~/.windsurf/`) | Not yet wired | Blocking is confirmed exit-code-2-plus-stderr only, reaching a human-visible log, never the model's context - a real vendor ceiling, not just unverified. |
| OpenAI Codex CLI | **MCP TOOLS ONLY** | Yes (`--global` only - no project-scoped variant) | Yes | No hook/interception mechanism confirmed to exist for this product at all - registers `whoami`/`read_document`/`materialize_document` in `~/.codex/config.toml`, **zero enforcement**. Shares that file with ChatGPT Desktop below. See `docs/known-gaps.md`. |
| ChatGPT desktop app | **MCP TOOLS ONLY** | Yes (`--global` only) | Yes | Same file, same limitation, as Codex CLI above - confirmed via OpenAI's own docs that both products read `~/.codex/config.toml`. See `docs/known-gaps.md`. |
| Cline | **NO ADAPTER** | N/A | N/A | Hooks are Windows-unsupported today. See `docs/known-gaps.md`. |
| Continue.dev | **NO ADAPTER** | N/A | N/A | No pre-tool-use hook primitive exists. See `docs/known-gaps.md`. |

Full detail: `docs/coverage-matrix.md`. **Read it before trusting anything
beyond Claude Code/VS Code in a real rollout** - "built to spec" is not the
same claim as "confirmed against the real agent." "Direct MCP tools" means
`mcp_local_server.py`'s whoami/read_document/login are registered as
directly callable tools for that agent, independent of the hook - not yet
done for Cursor/Windsurf/Copilot CLI, tracked as an open item. Gemini CLI
support was removed entirely (deprecated by its own vendor's successor
product, "Antigravity" - deliberately not supported either).

## Install

```
pip install -e .          # from a checkout of this directory, or
pipx install <wheel-or-git-url>   # recommended once published somewhere - see "Distribution" below
```

### Running `pabel-connector` after install (Windows/PowerShell gotcha)

`pip install -e .` puts `pabel-connector.exe` inside whatever venv's
`Scripts\` directory you installed into - PowerShell does **not** put that
on `PATH` just because the install succeeded, so a bare `pabel-connector
...` command right after installing commonly fails with `The term
'pabel-connector' is not recognized...`. Three equally correct fixes, pick
one:

```powershell
# 1) Activate the venv once per shell session - after this, bare
#    `pabel-connector ...` works for every command below.
.\.venv\Scripts\Activate.ps1

# 2) Or call the exe by its full path every time, no activation needed:
.\.venv\Scripts\pabel-connector.exe install <agent> ...

# 3) Or invoke it as a module with that venv's own python - works
#    regardless of PATH or activation state, the most foolproof option:
.\.venv\Scripts\python.exe -m pabel_connector.cli.main install <agent> ...
```

All three run the exact same code - pick whichever fits how you're already
working. The rest of this document writes bare `pabel-connector` for
brevity; substitute whichever of the three forms above actually resolves
on your machine.

### Which directory you run this from

For every agent **except** Codex CLI/ChatGPT desktop (below), `--dir`
matters: it's the project whose hook config gets written, and `--global`
(where supported) writes to a user-level location instead. For Codex
CLI/ChatGPT desktop, neither matters - both always write to the one
shared `~/.codex/config.toml`, unconditionally, regardless of which folder
your shell happens to be in or which project either product currently has
open. `--global` is still required for those two (see the table below),
but only because there is no project-scoped alternative to fall back to -
not because the current directory affects where anything lands.

### Per-agent quickstart (copy-paste ready)

Set these once per shell session first (PowerShell syntax - use `export`
instead of `$env:...=` on macOS/Linux):

```powershell
$env:PABEL_KEYCLOAK_URL      = "<your deployment's value>"
$env:PABEL_KEYCLOAK_REALM    = "<your deployment's value>"
$env:PABEL_KEYCLOAK_CLIENT_ID = "<your deployment's value>"
$env:PABEL_SERVER_URL        = "<your deployment's value>"
```

Then, per agent - `<client-id>`/`<client-secret>` come from your admin
(`server/agents_admin.py create-installation <agent>`, see below):

| Agent | Command |
|---|---|
| Claude Code | `pabel-connector install claude-code --dir . --client-id <id> --client-secret <secret>` (or `--global` instead of `--dir .`) |
| VS Code | `pabel-connector install vscode --dir . --client-id <id> --client-secret <secret>` (`--global` not supported - no confirmed user-level location) |
| GitHub Copilot CLI | `pabel-connector install copilot-cli --dir . --client-id <id> --client-secret <secret>` (or `--global`) |
| Cursor | `pabel-connector install cursor --dir . --client-id <id> --client-secret <secret>` (or `--global`) |
| Windsurf/Cascade | `pabel-connector install windsurf --dir . --client-id <id> --client-secret <secret>` (or `--global`) |
| Codex CLI | `pabel-connector install codex-cli --global --client-id <id> --client-secret <secret>` (`--global` is mandatory - `--dir` is rejected, see above) |
| ChatGPT desktop app | `pabel-connector install chatgpt-desktop --global --client-id <id> --client-secret <secret>` (`--global` is mandatory, same reason) |
| Cline / Continue.dev | `pabel-connector install cline --dir . --client-id <id> --client-secret <secret>` (prints why there's no hook to wire - see `docs/known-gaps.md` - still stores the credential) |

Verify anything above actually landed:

```powershell
pabel-connector list      # every registered agent, its status, --global support
pabel-connector doctor    # env vars, login status, and (where applicable) hook wiring
```

`--client-id`/`--client-secret` are this specific installation's own
Keycloak `client_credentials` credential - an admin creates it
(`server/agents_admin.py create-installation <agent>`, which itself needs
the agent product registered first via `agents_admin.py add <agent> ...`
if it isn't already - see `server/README.md`) and hands both values to you
out of band; `install` only ever stores what it's given (prompting for the
secret with hidden input if you omit it from the command line). This is
what proves *which installation* is calling on every relay - see
`server/README.md` and `docs/phase2-engineering-notes.md` for why a single
shared server can no longer just trust whichever URL it was reached at.

## Configure

Every agent needs the same environment variables (the installer prints
this list after each `install`):

- `PABEL_KEYCLOAK_URL` / `PABEL_KEYCLOAK_REALM` / `PABEL_KEYCLOAK_CLIENT_ID`
  - used by the relay's own login (see "Log in" below); must match the
  realm/client the deployed server trusts.
- `PABEL_SERVER_URL` - the deployed PABEL server's streamable-http URL.
  One shared server serves every agent product and every installation of
  it (see `server/compose.yml`) - genuinely one global value, the same
  for every agent installed on a given machine.

Each installation also needs its own agent credential (`--client-id`/
`--client-secret` at install time, above) - never an env var, since it's
specific to one installation rather than shared across a whole agent
product.

## Log in (one-time, then as needed)

```
pabel-connector login
pabel-connector logout
pabel-connector doctor   # check env vars + login status
```

Opens your system browser at Keycloak's hosted login page (MFA included,
whatever the realm requires) and saves a session every adapter's relay
call reuses and refreshes automatically.

**Note - three separate credentials are in play, not one**: `install`'s
`--client-id`/`--client-secret` establishes *which agent installation*
this is (server-verified on every relay call - see "Configure" above);
this login establishes *which human* you are, used only by the
enforcement adapter/hook (the mechanism that substitutes real content for
a blocked direct file read). An agent's own MCP client may separately
handle authentication for its passively-registered `whoami`/`read_document`
tools (if the model ever calls them directly) - all three ultimately
present credentials the same deployed server verifies for real, so none
of this is a security gap, just the accounting for what "logged in" and
"installed" actually mean here.

## Distribution

From v0.1.0 on, this package is released as a wheel attached to a GitHub
Release - `pip install <url-to-the-.whl-asset>` needs nothing but that one
file, no repo clone. `pip install -e .` from a checkout still works too,
for development. An internal package index (so a bare `pip install
pabel-connector` works without a URL) is deliberately out of scope for
this project - not a pending task, just a decision left to whoever runs a
real company-wide rollout, if that ever happens.

## Verifying an adapter against a real install

`docs/verification-procedure.md` is a fixed checklist for exactly this -
what to install, what to test, what "actually working" (not just "denies
the call") means, and how to record the result so it's comparable across
testers and agents. Run it before changing any adapter's status away from
UNVERIFIED.

## Known open items

See `docs/known-gaps.md` for Cline/Continue.dev/Codex CLI/ChatGPT desktop, and
`docs/coverage-matrix.md` for exactly what's confirmed vs. assumed for
every other adapter. In short: Claude Code and VS Code have both been
tried against a real, live install and work end-to-end; every other
adapter's path/schema has at least been re-checked against current
official docs (a live VS Code attempt originally found its first guess was
simply wrong, which prompted re-verifying all of them; several other real
bugs turned up and are already fixed - see coverage-matrix.md), but none of
Cursor/Windsurf/Copilot CLI has been exercised end-to-end
against a real agent session yet, and none of them has `mcp_local_server.py`
wired in as directly-callable tools yet either (Claude Code and VS Code
only, so far). Windsurf is structurally limited (confirmed no channel to
relay content to the model, not just unverified) regardless of further
testing.
