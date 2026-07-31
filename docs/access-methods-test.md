# Accessing `documents/Test.abe`: every method tried, and what came back

This document records a real test, run in this session, of every way an
AI coding agent (specifically, this Claude Code session, working in this
repository) could attempt to touch `documents/Test.abe` - the project's
demo CP-ABE fixture (3 sections, policies `security_specialist and
agent_claude_code`, `livello >= 2 and agent_claude_code`, `ruolo_dev`).
Nothing here is simulated after the fact - each result below is the actual
tool output from this session.

**Important context**: this repository's *own* Claude Code configuration
(`.claude/settings.json`) is deliberately permissive at the tool level -
only `Bash` calls to an OpenABE CLI binary are blocked (see
`.claude/hooks/block_abe_direct_read.py`). Raw ciphertext isn't the
protected secret in this project (that's the whole point of ABE); *local
decryption* is. The Claude Code **plugin** (`claude-plugin/`) enforces a
much stricter policy - it blocks *any* tool from touching an `.abe` file
at all - but it isn't installed onto this dev repo itself, so methods 1-4
below succeed here in a way they wouldn't for an employee using the
plugin. Method 9 demonstrates that contrast directly.

## 1. `Read` tool

```
Read(documents/Test.abe)
```

**Result: succeeded**, returned the file's raw JSON verbatim - the
`format`, the 3 `sections`, and each `groups[].policy` string in plain
text plus its `ciphertext` as a base64 blob (the CP-ABE ciphertext itself,
armored - `-----BEGIN ABE CIPHERTEXT BLOCK-----` etc.). Nothing here is
plaintext of the protected content; it's exactly what's already committed
to the repo.

## 2. `Grep` tool

```
Grep(pattern="policy", path=documents/Test.abe)
```

**Result: succeeded**, returned the three matching lines:

```
16:      "policy": "security_specialist and agent_claude_code",
20:      "policy": "livello >= 2 and agent_claude_code",
24:      "policy": "ruolo_dev",
```

Confirms Grep can search inside an `.abe` file exactly like any other text
file - again, not a leak, since these policy strings are already visible
via `Read` and are meant to be legible (ABE policies aren't secret; they
describe *who* can decrypt, not the content itself).

## 3. `Glob` tool

```
Glob(pattern="*.abe", path=documents/)
```

**Result: succeeded**, returned `documents\Test.abe`. Only confirms the
file's existence/name - no content at all. Included for completeness since
it's one of the tool types the plugin's hook explicitly denies (an
ambiguity case, since Glob can't name one single concrete file even when
it only matches one).

## 4. `Bash cat`

```bash
cat "documents/Test.abe"
```

**Result: succeeded**, identical raw JSON to method 1, via the shell
instead of the Read tool. Same non-finding: this repo's own hook doesn't
inspect `Read`/`Bash cat` content at all, only specific Bash *commands*
(see method 5).

## 5. `Bash` - direct OpenABE CLI decryption attempt

```bash
oabe_dec -s CP -k alice.key -i documents/Test.abe -o /tmp/plaintext.txt
```

**Result: blocked**, before OpenABE ever ran:

```
PreToolUse:Bash hook error: [python "$CLAUDE_PROJECT_DIR/.claude/hooks/block_abe_direct_read.py"]:
This invokes an OpenABE CLI binary directly. Decryption/keygen must happen inside the 'pabel'
MCP server (server/core.py), which combines the current user's *and* the calling agent's
attributes into one key and audits the result - calling oabe_dec/oabe_keygen/oabe_setup
directly would bypass both.
```

This is the one thing this repo's own hook *does* enforce: no local
`oabe_dec`/`oabe_keygen`/`oabe_setup` invocation, regardless of whether a
valid key even exists locally to make it succeed. The command above would
have failed anyway (no `alice.key` file exists in this working directory)
- the hook denies it before that would even matter.

## 6. Direct MCP tool call - `whoami`

```
mcp__pabel__whoami()
```

**Result: failed** - no live session available this session:

```
Error executing tool whoami: the logged-in session has expired or been revoked;
log in again (python login.py)
```

This is the server re-verifying a Keycloak-issued bearer token on every
call, exactly as designed (`server/core.py`) - there was no valid,
non-expired login for this repo's registered stdio MCP connection at the
time of this test. Reproducing this test with a real decrypted result
requires running `python server/login.py` (or `pabel-connector login`)
interactively first - something only a human can do (it opens a system
browser for Keycloak's own MFA-capable login page), not something an
agent can complete on its own.

## 7. Direct MCP tool call - `read_document` (the sanctioned path)

```
mcp__pabel__read_document(content=<base64 of Test.abe's raw bytes, 15848 chars>, name="Test.abe")
```

**Result: failed**, same wall as method 6:

```
Error executing tool read_document: the logged-in session has expired or been revoked;
log in again (python login.py)
```

Notable: this payload (15,848 base64 characters) is well above the
~10-12K character threshold where a previously-documented transport bug
truncated payloads on this call path (`docs/phase2-engineering-notes.md`
§6.6). That bug could **not** be re-confirmed or ruled out this time,
because the server checks authentication before it ever gets to decoding
`content` - the call failed at the auth stage, before truncation would
even become visible. This remains an open item, unchanged from before this
test.

## 8. `Write` / `Edit` tools - not live-tested

Both are technically unblocked in this repo's own configuration -
`.claude/settings.json`'s `PreToolUse` hook matcher is `Bash` only, so a
`Write`/`Edit` on `documents/Test.abe` would never even reach
`block_abe_direct_read.py`. This was **not** exercised live, to avoid
mutating the committed fixture during a test; it's stated here from
reading the hook's matcher configuration directly, not inferred. (The
Claude Code **plugin**, by contrast, denies `Write`/`Edit` on any `.abe`
file outright and unconditionally - see method 9 and
`connector/src/pabel_connector/core/decide.py`'s `DENY_MUTATING` branch.)

## 9. Simulating the Claude Code **plugin**'s enforcement (for contrast)

Methods 1-4 succeeded above only because the plugin isn't installed onto
this dev repo. Feeding the exact same `Read` attempt through the plugin's
actual hook script (`claude-plugin/pabel/hooks/pabel_relay_hook.py`) shows
what an employee running the plugin would actually experience:

```
$ echo '{"tool_name": "Read", "tool_input": {"file_path": ".../documents/Test.abe"}}' \
    | python claude-plugin/pabel/hooks/pabel_relay_hook.py
```

**Result: denied**, with an attempted relay (which itself hit the same
missing-login wall as methods 6-7):

```json
{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
"permissionDecisionReason": "Not authenticated to the PABEL server yet: not logged in to PABEL yet - run the connector's login command first (see README.md)"}}
```

This is the key contrast in this whole document: under the plugin, method
1's plain `Read` would never have returned raw ciphertext at all - it
would have been blocked and (with a valid login) transparently replaced
with the actual `read_document` result, sections decrypted or marked
`[ACCESS DENIED]` per policy. Every method 1-4 result above is specific to
*this repository's own, deliberately permissive, dev-time configuration* -
not a property of PABEL in general.

## Summary

| # | Method | Outcome | Why |
|---|---|---|---|
| 1 | `Read` | Raw ciphertext returned | This repo's hook doesn't inspect Read at all |
| 2 | `Grep` | Raw ciphertext lines returned | Same as above |
| 3 | `Glob` | Filename only | Glob never returns content anyway |
| 4 | `Bash cat` | Raw ciphertext returned | Same as method 1, via shell |
| 5 | `Bash oabe_dec` | **Blocked** | This repo's hook *does* block direct OpenABE CLI calls |
| 6 | `mcp__pabel__whoami` | **Failed - not logged in** | No valid Keycloak session this session |
| 7 | `mcp__pabel__read_document` (direct) | **Failed - not logged in** | Same auth wall; truncation bug (§6.6) unable to be re-tested as a side effect |
| 8 | `Write`/`Edit` | Not tested (unblocked by config, confirmed by inspection) | Hook matcher is Bash-only in this repo |
| 9 | Plugin-mediated `Read` (simulated) | **Blocked + relay attempted** | Shows the actual enforced experience once the plugin is installed |

**To get a real, decrypted `read_document` result** (methods 6, 7, and 9
all stopped at the same point), a human needs to run `python
server/login.py` or `pabel-connector login` interactively first - the
browser-based Keycloak MFA login can't be completed by an agent on its
own. That would be the natural next step to fully close out this test.
