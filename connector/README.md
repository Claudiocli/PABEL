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

| Agent | Status | Notes |
|---|---|---|
| Claude Code | **VERIFIED** | Confirmed end-to-end against a real deployed server. Install via the existing `claude-plugin/pabel/` plugin - see `pabel-connector install claude-code`. |
| VS Code native agent hooks (Preview) | UNVERIFIED | Same schema as Claude Code per docs; not tried live (needs a paid Copilot subscription). |
| GitHub Copilot CLI | UNVERIFIED | `additionalContext` unreliable per open vendor issues - content folded into the deny reason as a robust fallback. |
| Cursor | UNVERIFIED | 3 hook points (read/shell/MCP); no pre-write-block hook exists (low-impact, no legit write path anyway). |
| Windsurf/Cascade | UNVERIFIED | 4 hook points; whether relayed content reaches the model at all (vs. only a log) is unconfirmed - least-trusted adapter here. |
| Gemini CLI | UNVERIFIED | Catch-all `BeforeTool` matcher; content folded into the deny reason. |
| OpenAI Codex CLI | **DEGRADED** | Vendor limitation: hooks only fire for the Bash tool - native file reads are not interceptable at all today. |
| Cline | **NO ADAPTER** | Hooks are Windows-unsupported today; see `docs/known-gaps.md`. |
| Continue.dev | **NO ADAPTER** | No pre-tool-use hook primitive exists; see `docs/known-gaps.md`. |

Full detail and sources: `docs/coverage-matrix.md`. **Read this table before
trusting anything but Claude Code in a real rollout** - "built to spec" is
not the same claim as "confirmed against the real agent."

## Install

```
pip install -e .          # from a checkout of this directory, or
pipx install <wheel-or-git-url>   # recommended once published somewhere - see "Distribution" below
```

Then, per agent:

```
pabel-connector list                     # see every registered agent and its status
pabel-connector install <agent> --dir .  # wire the hook into the given project directory
pabel-connector uninstall <agent> --dir .
```

`claude-code` is a special case: it already has a dedicated, tested plugin
(`claude-plugin/pabel/`) using Claude Code's own marketplace mechanism -
`pabel-connector install claude-code` just prints those install steps
rather than writing a competing hooks.json by hand.

## Configure

Every agent needs the same environment variables (the installer prints
this list after each `install`):

- `PABEL_KEYCLOAK_URL` / `PABEL_KEYCLOAK_REALM` / `PABEL_KEYCLOAK_CLIENT_ID`
  - used by the relay's own login (see "Log in" below); must match the
  realm/client the deployed server trusts.
- `PABEL_SERVER_URL` - the deployed `mcp-server-<agent>` container's
  streamable-http URL. **Not one global value once more than one agent is
  in play** - each deployed server container is agent-specific (see
  `server/compose.yml`). If a machine runs more than one enforced agent
  against different deployed servers at once, override per agent as
  `PABEL_SERVER_URL__<AGENT_KEY>` (e.g. `PABEL_SERVER_URL__CURSOR`),
  uppercased with `-` replaced by `_`.

## Log in (one-time, then as needed)

```
pabel-connector login
pabel-connector logout
pabel-connector doctor   # check env vars + login status
```

Opens your system browser at Keycloak's hosted login page (MFA included,
whatever the realm requires) and saves a session every adapter's relay
call reuses and refreshes automatically.

**Note - two separate logins can exist**: this login is used only by the
enforcement adapter/hook (the mechanism that substitutes real content for
a blocked direct file read). An agent's own MCP client may separately
handle authentication for its passively-registered `whoami`/`read_document`
tools (if the model ever calls them directly with content it already
holds) - both ultimately present a token the same deployed server verifies
identically, so this isn't a security gap, just an extra, currently
unavoidable, one-time prompt per agent.

## Distribution

This package isn't published anywhere yet - `pip install -e .` from a
repo checkout is the only path today. Before a real employee rollout,
this needs either an internal package index or a pinned git URL; tracked
as an open item, not solved here, since it's an infrastructure decision
for whoever runs the company's deployment, not a design question for the
connector itself.

## Verifying an adapter against a real install

`docs/verification-procedure.md` is a fixed checklist for exactly this -
what to install, what to test, what "actually working" (not just "denies
the call") means, and how to record the result so it's comparable across
testers and agents. Run it before changing any adapter's status away from
UNVERIFIED.

## Known open items

See `docs/known-gaps.md` for Cline/Continue.dev, and `docs/coverage-matrix.md`
for exactly what's confirmed vs. assumed for every other adapter. In
short: only Claude Code has been tried against a real, live install this
session - every other adapter should be spot-checked against its real
agent before being trusted in production, starting with Windsurf (least
confirmed) and Codex CLI (structurally limited by the vendor, not by this
design).
