# Verification procedure: confirming an adapter against a real agent

Every adapter in this package except `claude-code` is labeled UNVERIFIED,
DEGRADED, or a documented gap (`docs/coverage-matrix.md`,
`docs/known-gaps.md`) - built strictly from vendor documentation, never
tried against the real thing. This procedure is what closes that gap for
one agent: a fixed sequence anyone - human or a fresh agent session with
no memory of this project - can follow right after cloning the repo, so
results from different testers are comparable and nothing important gets
skipped. Follow it as written, then add whatever is specific to the agent
under test at the end (its own quirks, extra tool types, anything this
procedure doesn't anticipate) - the fixed part exists so the *comparable*
part of the result is never missing, not to limit what you check.

## 0. Prerequisites

- A PABEL server already deployed and reachable (this procedure does not
  cover setting that up - see `server/README.md`). Have its
  streamable-http URL ready.
- The agent under test installed and working on this machine, independent
  of PABEL entirely (confirm it runs at all before wiring anything in -
  don't debug two unknowns at once).
- A Keycloak login you can complete interactively (browser + MFA) for a
  demo user (alice/bob/charlie - see `server/README.md`).
- This repo cloned, `pip install -e connector` run once.

## 1. Install

```
pabel-connector install <agent-key> --dir .
```

Record, verbatim:
- The exact `<agent-key>` used (see `pabel-connector list`).
- The file the installer wrote to, and its full contents (paste it into
  your result - this is the first thing to check if anything below
  doesn't work: wrong path or wrong schema shows up here first).
- Any error the install step itself produced.

## 2. Set environment and log in

```
pabel-connector login
```

Record:
- Whether the browser login flow actually opened and completed.
- The exact `PABEL_SERVER_URL`/`PABEL_KEYCLOAK_*` values used, and *how*
  the agent under test actually received them (an `env` block in a config
  file? inherited from the shell? something else?) - this is unconfirmed
  for several agents (see `docs/coverage-matrix.md`'s open questions) and
  is itself a result worth recording even if login succeeds.

## 3. Restart/reload the agent

Whatever "pick up the new hook config" means for this specific agent (a
full restart, a reload command, nothing at all because it polls the file)
- record which one it turned out to be.

## 4. The standard test matrix

Run each row that applies to this agent (not every agent has every tool
shape - e.g. Cursor has no write hook, Codex CLI only fires on Bash). For
each row, record the **raw output** (not a paraphrase) and which of these
three levels it reached:

- **L1 - fires at all**: something observably different happens versus
  the agent with no hook installed.
- **L2 - blocks correctly**: the direct raw-content attempt is denied.
- **L3 - relay confirmed**: the model actually receives the real
  decrypted-or-denied section content, not just a bare denial - this is
  the one that matters most and is the least likely to already work,
  since it depends on a content-injection channel most adapters guessed
  at (see each adapter's own docstring).

| Attempt | Target | What to check |
|---|---|---|
| Read-equivalent | `documents/Test.abe` | L1/L2/L3 as above |
| Grep/search-equivalent, if this agent has one | same file | same |
| Shell `cat`/`type`-equivalent | same file | same |
| A directory listing / glob on `documents/` | the folder | should deny as ambiguous, not relay (no single file) |
| Write/edit-equivalent, if this agent has a pre-write hook | same file | should deny, `write_target`-specific (see below) |
| Write/edit-equivalent | an unrelated file whose *content* happens to mention `.abe` or `documents/` | **must be allowed** - this is the write_target regression, see `docs/access-methods-test-after-fix.md` for why it matters |
| A completely unrelated file | anything else in the repo | must pass through with no observable effect at all |
| Direct call to this project's own `whoami`/`read_document` MCP tools, if the agent's own client lets you invoke them directly | n/a | must be **allowed**, not caught by the same detection - the own-tool allowlist (`core/decide.py`) |
| Shell invocation of `oabe_dec`/`oabe_keygen`/`oabe_setup`, if this agent has a shell/Bash-equivalent hook | any arguments | must be denied regardless of whether the arguments are even valid |

## 5. Report back

For each row, state the level reached (L1/L2/L3) and paste the raw
output. Where the result contradicts what the adapter's own docstring or
`docs/coverage-matrix.md` assumed, say so explicitly - a wrong assumption
found and corrected is exactly what this procedure exists to surface, not
something to smooth over. Update:

- The adapter's `STATUS` line/docstring in
  `connector/src/pabel_connector/adapters/<agent>.py` (and its installer,
  if the config path/shape needed correcting).
- `docs/coverage-matrix.md`'s entry for this agent.
- `registry.py`'s inline status comment.

Only change VERIFIED after L3 is actually confirmed - L1/L2 alone means
"the block works," not "the intended UX works," and this package's whole
point is the second one.

## 6. Whatever is specific to this agent

Add it here, in your own result, not in this file - a Cursor tester might
also want to try `beforeMCPExecution` with a tool name containing an
underscore (a known fragility, see `adapters/cursor.py`); a Windsurf
tester should specifically try to determine whether stderr from a denied
hook reaches the model's context at all (the single biggest open question
for that adapter). This procedure gives you the floor, not the ceiling.
