# Known gaps

Two agents have no enforcement adapter in this package at all, by design -
not oversights. `pabel-connector install <name>` for either prints this
same explanation rather than silently no-op-ing or erroring confusingly.

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
