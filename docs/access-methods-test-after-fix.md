# Accessing `documents/Test.abe`, take two: after fixing this repo's own hook

This is the same test as
[`docs/access-methods-test.md`](access-methods-test.md), re-run in the
same session immediately after fixing the gap that document exposed: this
repository's own `.claude/` configuration only blocked direct OpenABE CLI
invocations, not Read/Grep/Bash-cat on a raw `.abe` file - inconsistent
with the actual project requirement ("block every operation on `.abe`
files except sending it to the server") that the Claude Code **plugin**
(`claude-plugin/`) already enforced. The fix: this repo's own hook
(`.claude/hooks/pabel_relay_hook.py`, replacing the old
`block_abe_direct_read.py`) now dispatches into the same shared
`pabel-connector` core the plugin uses, with a catch-all `PreToolUse`
matcher (no tool-name restriction) instead of `Bash`-only, plus the
matching `PABEL_SERVER_URL`/`PABEL_KEYCLOAK_*` environment variables in
`.claude/settings.json` so the relay has somewhere to call.

## A real bug found and fixed while wiring this up

Fixing the read-blocking gap surfaced a second, independent bug: the
shared core's mutating-tool check (`DENY_MUTATING`) scanned the **entire**
tool call payload for an `.abe`/`documents/` mention, not just the file
actually being written to. That meant writing this very documentation -
which discusses `documents/Test.abe` in prose - was itself denied as if
it were an attempt to overwrite an encrypted file. Fixed in
`connector/src/pabel_connector/core/types.py`/`decide.py`: a new
`NormalizedCall.write_target` field (populated by each adapter from the
specific path a `Write`/`Edit`/`NotebookEdit`-shaped call actually targets)
is now what `DENY_MUTATING` checks, never the call's full content. Two
regression tests added (`connector/tests/test_decide.py`) confirm a write
whose *content* mentions an `.abe` path is allowed, while a write whose
*target* is one is still denied.

## Results, method by method

| # | Method | Before this fix | After this fix |
|---|---|---|---|
| 1 | `Read` | Raw ciphertext returned | **Denied**, relay attempted (stopped at "not logged in" - no live session this session) |
| 2 | `Grep` | Raw ciphertext lines returned | **Denied**, relay attempted (same auth wall) |
| 3 | `Glob` | Filename only (never blocked - can't relay a pattern) | **Denied** - ambiguous, no single concrete file (unchanged - Glob was never relayable) |
| 4 | `Bash cat` | Raw ciphertext returned | **Denied**, relay attempted (same auth wall) |
| 5 | `Bash oabe_dec` | Blocked | Still **blocked** (unchanged - this check never depended on the gap being fixed) |
| 6 | `mcp__pabel__whoami` (direct) | Failed - not logged in | Unchanged - still **allowed through** to the server (own-tool allowlist), still fails on missing login |
| 7 | `mcp__pabel__read_document` (direct) | Failed - not logged in | Unchanged - same allowlist, same missing-login wall |
| 8 | `Edit` on the fixture | Not tested (config allowed it) | **Denied** - mutating, `write_target` is the fixture itself |
| 9 | `Write` of unrelated content mentioning `.abe` in prose | N/A (bug didn't exist as a distinct case yet) | **Allowed** - proves the `write_target` fix; this file was itself written under the fixed hook without incident |

Raw output, `Read`-shaped call:

```json
{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
"permissionDecisionReason": "Not authenticated to the PABEL server yet: not logged in to PABEL yet - run the connector's login command first (see README.md)"}}
```

Identical shape (same reason text) for `Grep` and `Bash cat` - all three
now reach the same relay path methods 6-7 already hit, rather than
returning raw content. `Edit` on the fixture:

```json
{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
"permissionDecisionReason": "This project has no write/authoring path for .abe files - read_document on the deployed PABEL server is the only sanctioned operation."}}
```

## What this proves, and what it still doesn't

This repo's own dev-time Claude Code session now enforces the same
blocking policy the plugin does, closing the gap the first document
exposed. What it still doesn't prove: an actual **decrypted** result -
every read attempt above stopped at "not logged in," since no interactive
Keycloak browser login (MFA included) was performed this session. Running
`python server/login.py` or `pabel-connector login` once, interactively,
would let a follow-up test show the full round trip: `Read` denied, and
the correct sections (readable or `[ACCESS DENIED]` per policy) delivered
back via `additionalContext` instead.
