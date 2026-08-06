# Known gaps

Two agents (Cline, Continue.dev) have no enforcement adapter *and no install
action at all* - `pabel-connector install <name>` for either just prints an
explanation rather than silently no-op-ing or erroring confusingly. Two more
(Codex CLI, ChatGPT desktop app) sit in between: no enforcement adapter
either, but a real install action exists for MCP tool registration - see
their own sections below for why that split exists.

## OpenAI Codex CLI

Shipped as DEGRADED (Bash-only coverage) until a 2026-08 doc/issue-tracker
re-check - prompted by finding installers/vscode.py's own path guess was
simply wrong, which led to re-verifying every adapter in this package
rather than trusting the original research unverified - found a fact the
first pass missed: **Codex CLI's hooks feature is explicitly documented as
"experimental (disabled by default, not available on Windows)."** Not a
partial limitation like its Bash-only coverage - a platform this feature
does not run on at all.

This is the exact same blocking criterion already applied to Cline below:
employee machines can't be assumed non-Windows (this project's own dev
machine is Windows), so a hook surface unavailable there isn't a workable
adapter, regardless of how good its coverage would be on the platforms
where it does load (and even there, it would only ever cover the Bash
tool - Read/Write/Edit/Apply Patch/web fetch/MCP calls never reach a hook
at all per Codex's own docs).

**Unlike Cline/Continue.dev below, this isn't a pure no-op gap**: Codex CLI
shares its MCP server configuration (`~/.codex/config.toml`) with the
ChatGPT desktop app - confirmed via OpenAI's own docs
(`developers.openai.com/codex/mcp`), not assumed - so `pabel-connector
install codex-cli --global` registers `whoami`/`read_document`/
`materialize_document` as directly-callable MCP tools even though no hook
exists to relay a blocked read automatically. `status = "mcp-only"`, not
`"gap"` - see `installers/codex_family.py` for the shared implementation
and the naming-collision problem solved there (Codex CLI and the ChatGPT
desktop app each get their own MCP server name in that one shared file, to
avoid one product's install silently overwriting the other's).

**Revisit when**: Codex CLI ships Windows support for hooks - at which
point its Bash-only coverage gap (see the removed adapter's history in
`docs/phase2-engineering-notes.md`) would still apply and it would ship as
DEGRADED, not full coverage.

## ChatGPT desktop app

New 2026-08, never previously considered in this package. Confirmed via
OpenAI's own docs (`learn.chatgpt.com/docs/extend/mcp`) that the desktop
app's MCP server list lives in the exact same `~/.codex/config.toml` Codex
CLI reads above - not a coincidence to work around, a genuine shared
mechanism.

Unlike Codex CLI, this isn't a Windows-specific gap - **the desktop app has
no hook/tool-interception mechanism at all, on any platform.** What it does
have (`default_tools_approval_mode`/per-tool `approval_mode`,
`disabled_tools`) controls whether a tool call prompts the human or is
blocked outright, never what content a call returns - nothing that could
substitute a blocked read with the real decrypted result the way
`core/decide.py` does for every hook-based adapter in this package.

Same as Codex CLI: `pabel-connector install chatgpt-desktop --global`
registers `whoami`/`read_document`/`materialize_document` as
directly-callable MCP tools in the shared `config.toml` - `status =
"mcp-only"`, zero enforcement, no blocking of a direct encrypted-file
read. Kept as its own registry entry (not folded into codex-cli) so an
organization can authorize one product without the other via
`server/agents_admin.py`'s per-product `required_role`, despite the shared
file.

**Revisit when**: OpenAI ships any interception/approval mechanism for this
product that can substitute a blocked call's content, not just prompt or
block it outright.

## The one real mitigation for every agent in this file

None of the four agents below can be made to actually enforce the
block-and-relay workflow - not a gap in this package's effort, a confirmed
absence of any interception mechanism in the product itself (Codex
CLI/ChatGPT desktop: no hook, only `disabled_tools`/`approval_mode`, which
gate an entire tool, never substitute one call's content; Cline/Continue.dev:
see their own sections). Codex CLI does read a documented `AGENTS.md`
instruction file (`developers.openai.com/codex/guides/agents-md`,
global `~/.codex/AGENTS.md` or project-scoped) that could nudge a model
toward calling `read_document` instead of reading a document directly - but
OpenAI's own docs describe it explicitly as "instruction rather than
enforcement... not a security sandbox," the same honest limit already
attached to Claude Code's informational Skill (`docs/phase2-engineering-
notes.md` §19.2 of the earlier phase). It's a nudge a careless or
adversarial use ignores at no cost - not treated as a fix for this gap.

The only thing that actually closes the gap, rather than discourage walking
through it, is **not putting the encrypted documents on a machine that only
runs one of these four agents in the first place** - a deployment/topology
decision, not something `pabel-connector` can automate in code. If there is
no local ciphertext file to try reading directly, a model has no path to
content at all except the registered `read_document` tool - the gap isn't
mitigated, it's structurally absent. This trades off against convenience
(the document corpus has to live somewhere these machines can't browse to
directly, e.g. only reachable through whatever exposes it to the deployed
PABEL server) and is therefore a call for whoever plans a real rollout, not
a default this package can assume.

## Cline

Cline's hooks (`beforeTool`/`afterTool`, shipped v3.36+) are implemented as
a JS/TS **plugin SDK** loaded into Cline's own runtime - not a simple
external-command JSON config file like every other agent this package
supports. Building a real adapter would mean shipping a JS/TS plugin, a
different engineering surface than the rest of this package.

More importantly: **as of this writing, Cline's hooks are explicitly
macOS/Linux-only - there is no Windows support at all.** Since employee
machines can't be assumed non-Windows (this project's own dev machine is
Windows), building against a feature that doesn't run on a plausible chunk
of the target fleet isn't worth it yet.

**Revisit when**: Cline ships Windows support for hooks. Until then, Cline
can still be pointed at the deployed PABEL MCP server directly (`whoami`/
`read_document` become normal callable tools, same as any MCP client) -
there is just no enforcement, no blocking of direct `.abe` reads.

## Continue.dev

Continue has no pre-tool-use hook primitive at all. What exists instead:

- CLI (`cn`): a permission system (`~/.continue/permissions.yaml`,
  `--allow`/`--ask`/`--exclude` flags) that gates *whether* a tool call
  proceeds, with no way to substitute different content for a blocked one.
- IDE extension: a `ToolPolicy` enum (`allowedWithPermission`,
  `allowedWithoutPermission`, `disabled`) in its own Redux state - same
  limitation.

Neither gives a way to do what every other adapter in this package does:
transparently deny a direct read and hand the model the real, already
decrypted-and-access-controlled result in the same turn. The best
available fallback - disabling or requiring approval on file-read tools
near `.abe` files via `permissions.yaml` - stops accidental raw-ciphertext
exposure but requires the user to then manually invoke the passively
registered `read_document` MCP tool themselves; it is not the invisible
relay UX this package is otherwise built around.

**What still works regardless**: Continue.dev is an MCP client, so
registering the deployed PABEL server's `whoami`/`read_document` tools
works exactly like it does for every other MCP-compatible agent, hook or
no hook - `pabel-connector install continue-dev` prints how.

**Revisit when**: Continue.dev ships a pre-tool-use hook primitive with a
content-injection channel comparable to what the other adapters use.
