# PABEL Phase 2 — engineering notes: OpenABE-on-Linux, container design, and the agent-identity model for a remote server

These notes document the investigation and design decisions from the start
of Phase 2 (2026-07-29): moving the PABEL MCP server from a locally-spawned
stdio process (Phase 1) to a containerized service reachable over the
network. Written up for reference/reuse (including thesis material) — each
claim below is anchored to concrete evidence (commit hashes, file paths,
exact error text) rather than general impressions, since that's what makes
it re-checkable later.

## 1. Background: what PABEL is

PABEL is an MCP (Model Context Protocol) server that lets an AI coding
agent decrypt Ciphertext-Policy Attribute-Based Encryption (CP-ABE)
documents on a human's behalf, but only the sections whose policy is
satisfied by **both** the human's attributes (managed in Keycloak) *and*
the calling agent product's attributes (an admin-managed registry) —
never either principal alone. Phase 1 (built and verified end-to-end
2026-07-29) established: Keycloak with MFA-only login (no password-grant
fallback), PostgreSQL for key caching/agent registry/audit mirror, and a
FastMCP server over stdio, spawned fresh per Claude Code session. Phase 2's
goal is to run that server once, as a shared, containerized service, per
the original project brief ("the MCP server has to sit on a third-party
device").

## 2. Is OpenABE portable to Linux? Investigating a fork, not upstream

`Desktop/openabe` is not vanilla Zeutro OpenABE — `git remote -v` shows it
tracks `https://github.com/Claudiocli/openabe.git`, and `git log` shows
the account's own commits (`Claudio Lucisano <claudiocli.cp@gmail.com>`)
porting it forward:

| Commit | Date | Summary |
|---|---|---|
| `f9d9b61` | 2026-07-16 | "Porting OpenABE to support openssl 4.0.1 and relic 0.7.0" |
| `2725402` | 2026-07-17 | "Massive upgrade" — C++17, renamed RELIC APIs, gtest-latest compatibility |
| `c9794a9` | 2026-07-20 | "Fix Windows Install" — RELIC DLL install, wrong-OpenSSL linking, **a PRNG padding bug in `zprng.cpp` (`out_len` copied without padding)**, an NID-mapping fix in `zcontextpksig.cpp` |

**A methodological trap worth naming explicitly**: the repo also contains
two stale build artifacts, `outpt` (a full build log, timestamped
2026-07-17 12:16) and `test_result` (a gtest run, timestamped 2026-07-17
16:56) — both dated *before* commit `c9794a9`. Read naively, `test_result`
looks alarming: 16 of 46 tests failed, including
`CryptoBoxCPABEContext`/`CPATestsForCpAbeSchemeContext` — the exact CP-ABE
path this project depends on — and `CTR_DRBG` (a randomness test, which is
exactly the kind of thing a PRNG padding bug would break). Because these
logs predate the fix commit that explicitly targets a PRNG bug, and
because the actually-shipped Windows binaries (`openabe/bin/*.exe`,
mtimes 2026-07-20 11:35, ~12 minutes before the `c9794a9` commit) were
almost certainly built from that fixed state — corroborated directly by
this project's own Phase 1 testing, which exercised `oabe_keygen`/
`oabe_enc`/`oabe_dec` extensively with correct results across many policy
combinations — the historical failures are best read as **already-fixed
history**, not a live defect. The lesson: a failing test log's *date*
relative to the fix history matters as much as its content; don't let a
stale artifact stand in for re-running the thing.

### 2.1 Empirical Linux build, not a guess

Rather than reason abstractly about portability, the fork was built from
scratch in a throwaway `ubuntu:24.04` container (via Rancher Desktop /
`nerdctl`, cloning `Claudiocli/openabe` directly — deliberately not
copying the Windows working tree, to get a clean-room result). This
surfaced three real, Linux-specific build bugs, all unrelated to the
OpenSSL/RELIC porting work itself (each fixed only in the throwaway
container for this test, not in the upstream fork — that's your call):

**Bug A — `deps/install_pkgs.sh` targets a package that no longer exists.**
`main_ubuntu()` (line 210) does `install_package python-pip` and then hard
`fail()`s (`exit 1`) if a `pip` binary isn't found afterward (lines
212–214). Ubuntu dropped the Python-2-era `python-pip` package entirely by
24.04 ("noble") — only `python3-pip` exists — so `apt-get install
python-pip` fails, no `pip` binary materializes, and the *entire* script
aborts before installing any real build tool (gcc, make, cmake, etc.).
Worked around (for the container build) with `apt-get install -y
python3-pip && ln -sf /usr/bin/pip3 /usr/bin/pip` before invoking the
script — a symptom of an aging OS-detection script, not the C++ port.

**Bug B — `deps/gtest/Makefile`'s `cd $<` resolves empty.** The recipe
(line 15-20):
```makefile
googletest-release-$(VERSION)/.built: | googletest-release-$(VERSION)
	cd $<; \
	mkdir -p build && cd build/ && \
	cmake -DCMAKE_INSTALL_PREFIX:PATH="$(DEPS_INSTALL_ZROOT)" ../googletest/ && \
	...
```
depends on `googletest-release-$(VERSION)` only as an **order-only**
prerequisite (after `|`). On this Make (GNU Make 4.3, Ubuntu 24.04), `$<`
expanded to an empty string for that recipe — the build log shows the
literal line `cd ;`, which (bare `cd`) sends the shell to `$HOME`
(`/root` in the container) instead of the extracted gtest source
directory, so the subsequent `cmake ../googletest/` looked for
`/root/googletest` and failed: `CMake Error: The source directory
"/root/googletest" does not exist.` This most likely also latently exists
on Windows/MSYS2 — it probably never triggered there only because a
`.built` marker from an earlier build was already cached, so `make` never
re-ran that recipe on a truly clean tree. Worked around (for the
container) by replacing `cd $<;` with the explicit `cd
googletest-release-$(VERSION);`, avoiding reliance on `$<`'s
order-only-prerequisite behavior entirely.

**Bug C — `deps/relic/Makefile` unconditionally references a
Windows-only variable.** The recipe for `relic-$(VERSION)/.built`
(lines 39-40, 43-44) runs `$(MINGW_MAKE) && $(MINGW_MAKE) install` twice
(once per RELIC variant it builds, `bp` and `ec`). `MINGW_MAKE` is only
ever assigned a value inside `Makefile.common`'s `ifeq ($(OS),
Windows_NT)` branch (`MINGW_MAKE := /mingw64/bin/mingw32-make`, line 62)
— the Linux/Darwin branch (`else`, from line 77) never defines it. On
Linux this expands to nothing, leaving a bare `&&` where the recursive
make invocation should be: `/bin/sh: 3: Syntax error: "&&" unexpected`.
The straightforward fix is using GNU Make's own built-in `$(MAKE)`
(always correctly set to the running make program, on any platform)
instead of a custom variable that only one platform branch defines —
applied in the container as a 4-occurrence find/replace in that one file.

*(This file documents the finding; whether to also patch it upstream in
`Claudiocli/openabe` is your call, not made here.)*

**Bug D — `env`'s `LD_LIBRARY_PATH` doesn't account for `lib64`.**
Once compilation succeeded, `./test_libopenabe` failed at *launch*, not
in a test assertion: `error while loading shared libraries: libssl.so.4:
cannot open shared object file`. OpenSSL's own build system (`./config`,
the auto-detecting wrapper `deps/openssl/Makefile` invokes) installed its
shared libraries into `deps/root/lib64/` on this distro, not
`deps/root/lib/` — a common convention on 64-bit Linux, but not one
`env` accounts for: it only ever adds `.../lib` to `LD_LIBRARY_PATH`
(line 45), which is sufficient on MSYS2/Windows (no `lib64` split there)
but not here. Worked around by also adding `deps/root/lib64` and
`root/lib64` to `LD_LIBRARY_PATH` before running the test suite.

### 2.2 Result: all tests pass

With all four workarounds applied, `make` (deps + src + cli + examples)
and `make test` both completed with exit code 0 — including
`cli/runTest.sh`'s end-to-end CP-ABE scenario, which mirrors PABEL's own
usage almost exactly: generate the authority, generate keys for two users
with different attribute sets, encrypt under a composite policy
(`(Doctor or Nurse) and (Floor in (2-5))`), and confirm decryption
succeeds for the key that satisfies it and fails for the one that
doesn't. Since `make test`'s target chains every sub-test with `|| exit
1` (`Makefile:49-56`), reaching the later tests (`test_zsym`,
`cli/runTest.sh`) at all is itself proof the earlier gtest suite
(`test_libopenabe`, `test_zml`, `test_abe`, `test_pke`, `test_ske`) also
passed cleanly — none of the historical failures from §2 reproduce on a
clean Linux build.

**Bottom line: yes, your fork installs and passes its own test suite on
Ubuntu 24.04 without problems** — every issue encountered was in a build
script (dependency install, two Makefiles, one environment variable),
never in the ported cryptography itself.

### 2.3 A real gotcha found only by actually deploying: authority/ciphertext files aren't portable across builds

Once the containerized server was actually running (§4-5 below), a
different problem surfaced that `make test` alone couldn't have caught:
`server/authority/org.mpk.cpabe`/`org.msk.cpabe` had been generated back
in Phase 1 by the **Windows** build (MSYS2/MinGW). The **Linux** build
inside the container could not use them at all - even `oabe_keygen -i
'dev'` (the simplest possible attribute, no policy complexity) failed
with `caught exception: Invalid function input`. Isolated step by step:

- A **fresh** authority generated by the Linux `oabe_setup` works fine
  with Linux `oabe_keygen`/`oabe_enc`/`oabe_dec` - a full keygen +
  encrypt + decrypt round trip inside the container succeeded cleanly.
- The **Windows** build can also read that same Linux-generated
  authority without issue (`abe.keygen('dev')` run locally succeeded
  against it) - so the incompatibility isn't symmetric, and isn't a
  question of "which platform is right," just that the specific
  Windows-generated authority already in place predated it working
  correctly and wasn't compatible going forward.
- Separately, a **document's ciphertext** is also tied to whichever
  build's `oabe_enc` produced it: a `test.abe` fixture encrypted by the
  Windows build failed to decrypt under the Linux build even once both
  were using the *same*, Linux-generated authority. Re-encrypting the
  same fixture with the Linux build's `oabe_enc` fixed it immediately.

**Practical takeaway**: authority files and any already-encrypted `.abe`
document are tied to the exact build that produced them, not just to
"the same source code." Regenerating the authority - and re-encrypting
anything under it - with the Linux build (the one the container
actually ships) resolved this, and the result works from both the
Linux container and local Windows/stdio testing going forward. This
matters operationally: whichever build mints the authority for a real
deployment is the one every document must also be encrypted with.

### 2.4 Two more container-specific fixes, unrelated to OpenABE

- **`FASTMCP_HOST` env var alone does nothing.** `FastMCP.__init__`
  always explicitly forwards its own `host="127.0.0.1"` Python default
  into its internal `Settings(...)`; an explicitly-passed constructor
  argument overrides pydantic-settings' env-var lookup entirely, so
  setting `FASTMCP_HOST=0.0.0.0` alone is silently ignored. Fixed by
  reading it in `mcp_server.py` and passing `host=` explicitly. Without
  this, the container listens on loopback-only and Docker's port mapping
  can't reach it at all (no error - just nothing on the far end).
- **Keycloak's `iss` claim reflects login hostname, not verifier
  hostname.** A token issued via a browser hitting Keycloak at
  `http://localhost:8080` carries `iss: http://localhost:8080/realms/pabel`
  - regardless of how some *other* consumer (the containerized MCP
  server, reaching Keycloak internally at `http://keycloak:8080`) would
  reach the same realm. `KeycloakAuth` originally derived both "how do I
  reach Keycloak" and "what issuer string do I expect" from one
  `KEYCLOAK_URL`; split into `KEYCLOAK_URL` (network reachability) and
  `KEYCLOAK_ISSUER_URL` (issuer-string match, defaults to `KEYCLOAK_URL`
  when unset - so this only needs setting when they genuinely differ,
  as in the containerized case). Getting this wrong produces a
  content-free `401 Unauthorized` with no server-side detail beyond
  `invalid_token` - worth knowing the shape of that failure mode if it
  recurs elsewhere.

### 2.5 Verified end to end

With all of the above applied, a real HTTP request bearing alice's
Keycloak-issued bearer token against the running
`mcp-server-claude-code` container correctly authenticates, resolves her
`agent_claude_code_user` realm role, and decrypts exactly the sections
her combined (user, agent) attributes satisfy - identical results to the
same test run over stdio. The audit trail (`audit_log` in Postgres)
records both transports' calls indistinguishably, as designed.

**Operational note, not yet fully resolved**: Keycloak's dev-mode
`start-dev --import-realm` re-imports the realm fresh on every
*recreation* (not every restart) of the keycloak container - anything
not declared in `realm-org.json` (an imperatively-created role, a user's
TOTP enrollment) is lost when that happens. The `agent_claude_code_user`
role is now declared directly in `realm-org.template.json` (with
`realmRoles` on alice's user entry) to survive this - written but not
yet exercised against an actual recreation, to avoid disrupting a
working session and forcing yet another TOTP re-enrollment. A user's
TOTP secret itself has no equivalent fix available (Keycloak doesn't
expose enrolling it via static import) - that's simply a cost of
recreating the Keycloak container in this dev-mode setup, not something
to route around.

## 3. Container architecture: one `compose.yml`, multiple containers

Considered literally bundling Postgres + Keycloak + the MCP server into
one container, since a single container was the initially-stated
preference. Recommended against it, and the user agreed multiple
containers are fine:

- Postgres and Keycloak are official, independently-maintained,
  security-patched images; folding them into a custom image makes this
  project responsible for re-implementing that maintenance.
- One-process-per-container is what lets each service restart/scale/fail
  independently — a crashing MCP server shouldn't take down the database
  or the identity provider.
- `nerdctl compose` (already in use for Postgres+Keycloak in Phase 1)
  already gives "one command brings everything up," which is the actual
  consolidation being asked for — it does not require merging processes.

Net: **one `compose.yml`**, three-plus containers (`postgres`, `keycloak`,
and one `mcp-server-<agent>` per registered agent product — see §4).

## 4. Agent identity in a shared, remote server — a design that changed mid-flight

### 4.1 Why Phase 1's mechanism doesn't carry over

Phase 1 identified the calling agent via `PABEL_AGENT_ID`/
`PABEL_AGENT_API_KEY` **environment variables** on a process spawned fresh
per Claude Code session. A shared remote server has one long-lived
process serving many users/agents at once — env vars are global to that
process, not per-request, so this mechanism has no meaning once the
server isn't spawned per-session.

### 4.2 First proposal: one Keycloak client per agent (superseded)

Since MCP's Authorization spec is OAuth-shaped, and `mcp.server.auth`
(the Python MCP SDK's bearer-token verification layer, confirmed present
in `mcp==1.28.1`) is designed to delegate verification to an external
IdP, the first design was: register each agent product as its own
Keycloak OAuth client, so a single verified bearer token would carry
*both* principals at once — the user as `subject`, the agent as the
token's `client_id` (a field `AccessToken` already models,
`mcp/server/auth/provider.py`). One shared container could then serve
every agent, differentiated purely by which client_id issued the
presented token.

This was reconsidered after directly researching how Claude Code's own
MCP client behaves as an OAuth client (not just what the MCP spec
*allows*), which surfaced two problems:
- Claude Code defaults to **OAuth Dynamic Client Registration (RFC
  7591)** for servers that support it — meaning the `client_id` Claude
  Code would end up using is a value *Keycloak generates at registration
  time*, not a stable, predictable string this project could key a
  lookup table on. (Pre-configured client credentials are supported as an
  alternative, but that's opt-in configuration, not the default path.)
- How Claude Code isolates OAuth state when several *different* remote
  MCP servers are configured is **not documented** one way or the other —
  a real risk to build a security boundary on top of, given the option to
  sidestep it entirely (below).

### 4.3 Final design: realm role per agent, container instance per agent

- **One agent product = one container instance** (same image, one
  `docker compose`/`nerdctl compose` service each, e.g.
  `mcp-server-claude-code`), with `PABEL_AGENT_ID` baked into that
  instance's own environment — structurally the same idea Phase 1 already
  used, just scoped to a whole container instead of a per-session process.
  Each agent is therefore a genuinely distinct MCP server URL from Claude
  Code's point of view, which is the one thing that *is* clearly,
  reliably supported (multiple independent remote MCP server
  configurations), sidestepping the undocumented multi-server
  auth-isolation question in §4.2 entirely.
- **One shared Keycloak client** for human login (the one already built
  in Phase 1) — no per-agent OAuth client needed at all, and therefore no
  dependency on how any particular MCP client handles multi-client OAuth.
- **One Keycloak realm role per agent** (e.g. `agent_claude_code_user`),
  assigned per-user by an admin — this is what answers "can this
  *specific* user use this *specific* agent," which a flat registry
  (Phase 1's `agents` table) couldn't express. `schema.sql`'s `agents`
  table already changed accordingly: `api_key_hash` → `required_role`
  (the API-key mechanism is dropped entirely — Keycloak *is* the
  authentication now, nothing else needs to separately vouch for the
  agent).
- **Two different failure modes, deliberately**: an `agent_id` this
  server has never heard of at all is a hard, immediate deny (`AuthError`)
  — there's no legitimate reason an unregistered agent identity should be
  talking to this server. But a *known* agent whose required role the
  current user's token lacks contributes **zero attributes** to the
  combined ABE key rather than erroring — the same section-level,
  cryptographic, implicit-failure behavior already established in Phase 1
  for "agent doesn't exist," just now scoped to "this agent exists but not
  for this user." `whoami`/`list_documents` keep working either way;
  only agent-gated sections of `read_document` come back denied.

## 5. Status: done, verified end-to-end (§2.5). Remaining open items

- Declaring `agent_claude_code_user` in `realm-org.template.json` (§2.5)
  hasn't yet been exercised against an actual Keycloak container
  recreation - do that once, deliberately, next time a recreation is
  already planned (it costs every demo user's TOTP enrollment, so don't
  trigger one just to test this in isolation).
- Only `claude-code` has its own `mcp-server-<agent>` compose service so
  far. Proving "one container per agent" for a second, different agent
  (e.g. `generic-tool`, already registered in Postgres from testing)
  would confirm the pattern generalizes, not just works once.
- `nerdctl compose up -d --force-recreate <service>` cascades to
  recreating everything that service `depends_on`, including Keycloak -
  learned the hard way mid-session. Prefer targeted rebuild + recreate
  (`compose build <service>` then `compose up -d --force-recreate
  <service>` was still not enough by itself in one case - the safest
  known way to pick up a code change without touching Keycloak/Postgres
  is still being worked out) over a blanket `--force-recreate` once
  Keycloak/Postgres already hold state worth keeping.

## 6. A second iteration: `read_document`'s document-transport model (2026-07-30, later session)

Phase 2 as described above (§1-5) was verified end-to-end, but only ever
exercised by test fixtures that already lived in `PABEL/documents/`. The
first time this session actually tried the intended real use case - "here
is a file I found, decrypt what you can" - it exposed a genuine design gap
between what UC1 (`README.md`) specifies and what had been built.

### 6.1 The gap: UC1 says the agent *has* the file; the code required it to already be on the server

UC1's main scenario (`README.md`) is: the agent already has (or finds) the
encrypted file `f`, hands it to the MCP server, and gets back `f'`. The
Phase 2 `read_document(path: str)` tool instead required the file to
already exist under a server-owned folder (`core.DOCS_ROOT`, i.e.
`PABEL/documents/`), resolved and validated by `core.resolve_within_root()`.
That's workable when server and client share a filesystem (this project's
stdio transport, run locally), but it silently assumes something UC1 never
promised, and it has no meaning at all for the streamable-http transport
(§3-5) once server and client are genuinely different machines - a remote
container has no way to reach an arbitrary path on the human's laptop.

### 6.2 The incident that exposed it: an NTFS case-collision destroyed a test fixture

Asked to read `Desktop/Test.abe`, and finding it outside `DOCS_ROOT`, the
file was moved into `PABEL/documents/Test.abe` to satisfy the old
path-based tool. `PABEL/documents/` already contained a *lowercase*
`test.abe` - the fixture this session had earlier regenerated with the
Linux build (§2.3) and confirmed working end-to-end. Windows/NTFS resolves
paths case-insensitively, so `Test.abe` and `test.abe` are the same file:
the move silently overwrote the working fixture. Confirmed directly with
`stat`: both names resolved to the same inode, size (45251 bytes) and
mtime (2026-07-21 16:48:56, i.e. predating this session's authority
regeneration entirely).

This is exactly why the very next `read_document` call looked broken in a
confusing way: alice (`livello=3`) got `0/5` sections accessible, including
policies as weak as `livello >= 1`, which she trivially satisfies. That
symptom - *every* section denied, even ones that should trivially pass -
is the signature of a cryptographic/authority mismatch (§2.3), not a real
policy outcome; `abe.decrypt_bytes()` returns `None` identically whether
the key doesn't satisfy the policy *or* `oabe_dec` simply can't process
ciphertext from an incompatible build, so the tool has no way to
distinguish the two cases in its output. The file that had actually been
read was the pre-regeneration Desktop copy, encrypted under a since-
replaced authority - not a policy or permissions bug at all.

**Lesson, stated generally**: a path-based document store that a
same-machine agent and server both happen to share is a convenient
shortcut that quietly encodes an assumption (shared, case-sensitive
namespace) which breaks exactly when the architecture is used the way it
was actually designed to be used (agent hands over content it already
has, from wherever).

### 6.3 The fix: `read_document` takes content, not a path

`read_document`'s signature changed from `read_document(path: str)` to
`read_document(content: str, name: str = "document")`
(`server/mcp_server.py`). `content` is the `.abe` file's own raw text -
supplied by the caller, from wherever it found the file - and `name` is
now purely a free-text label for the response and the audit log, never
resolved against any path. Supporting changes:

- `server/document.py`: `load_abe(path)` → `load_abe(content)`, now
  `json.loads()`-ing the given text directly instead of reading a file.
- `server/core.py`: `resolve_within_root()` deleted outright (no longer
  used anywhere); `decrypt_document(target, key_bytes)` →
  `decrypt_document(content, key_bytes)`, returning just the section list
  (the document label now lives entirely in `mcp_server.py`, supplied by
  the caller rather than derived from a filename).
- `core.DOCS_ROOT` / `list_documents()` were deliberately **kept** as a
  read-only, browsable demo library (`PABEL/documents/*.abe`) - unrelated
  to how `read_document` now takes its input, useful only for a human or
  agent to see what test fixtures exist by name.

### 6.4 Consequence: the access-control hook's own threat model had to be reconsidered

`.claude/hooks/block_abe_direct_read.py` originally blocked *any* direct
`Read`/`Grep` of an `.abe` file or the `documents/` folder outright, plus
any `Bash` command combining such a path with a command that reads file
content (`cat`, `open(`, etc.) - on the theory that all decryption must go
through `read_document`. Once `read_document` requires the caller to
already hold the file's raw text, reading that raw text is no longer a
bypass of anything - it is now the *first, required step* of the
sanctioned flow. Re-examining what the hook actually protects: an `.abe`
file at rest is ciphertext plus policy strings only - `document.py`'s own
design already treats the policy/section shape as non-secret ("the
document's shape is not itself a secret", `mcp_server.py`'s
`read_document` docstring) - so reading it directly was never actually a
confidentiality leak, only a discipline mechanism. The real, meaningful
boundary is *local decryption*: invoking `oabe_dec`/`oabe_keygen`/
`oabe_setup` directly would bypass both the combined (user, agent) key
computation (`core.agent_session_key`) and the audit log entirely.

The hook was rewritten accordingly: it no longer touches `Read`/`Grep` at
all, and for `Bash` it now only blocks commands that invoke one of the
four `oabe_*` CLI binaries directly (`OABE_BINARY` regex,
`\boabe_(setup|keygen|enc|dec)\b`), still exempting this project's own
service scripts. `.claude/settings.json`'s `PreToolUse` matcher was
narrowed from `"Read|Bash|Grep"` to `"Bash"` to match. This is a case
where a security control had to be *loosened* deliberately, with the
loosening justified by what the file format actually does and doesn't
expose - not a bypass, a re-derivation of the same intent under a changed
architecture.

### 6.5 A transport bug: tool-call string arguments silently coerced to objects

The first live test of the new `content: str` parameter failed with a
Pydantic error surfaced from inside the running MCP server:
`content / Input should be a valid string [type=string_type,
input_value={'format': 'abe-doc', ...}, input_type=dict]`. The `.abe`
file's raw text is itself a JSON document - passing it verbatim as a tool
argument caused some layer between the calling agent and the MCP
transport to auto-parse a string value that happens to look like valid
JSON into a structured object, silently, regardless of the parameter's
declared `string` type in the tool's schema. (Confirmed the schema itself
was correct and the running process had picked up the new code first, via
a smaller reproduction with a short dummy payload, before concluding this
was a transport-level coercion rather than a code bug.)

Fixed by base64-encoding `content` before the call and decoding it inside
the tool (`base64.b64decode(content).decode("utf-8")`,
`server/mcp_server.py`) - base64 text never parses as JSON, so it always
survives as a literal string end to end. Verified against a real ciphertext
group (the `security_specialist and agent_claude_code` policy): the tool
returned the correct decrypted plaintext for alice via this exact path,
proving the new content-based contract works, not just that it compiles.

### 6.6 Known open limitation: large base64 payloads truncate

Payloads of 1,440 and 1,932 base64 characters round-tripped correctly
(including a real decrypt). A 12,428-character payload (one policy group
of the regenerated `test.abe`) consistently failed at the server with
`Incorrect padding` - i.e. the string that arrived was no longer a valid
multiple-of-4-characters base64 string, implying truncation somewhere
between constructing the argument and the server receiving it. The exact
layer responsible (the tool-call interface itself vs. some interstitial
size limit) was not isolated by binary search this session - noted here as
a real, reproducible limitation rather than resolved. **Practical
workaround used**: split a multi-policy-group document into one
`read_document` call per group (each group's ciphertext individually
stayed under the working size) rather than sending the whole file in one
call.

### 6.7 Operational note: editing a running stdio server's code has no effect until it's restarted

`mcp_server.py` is spawned once per Claude Code session (`.mcp.json`) and
kept alive for the session's duration; editing its source (or any module
it imports) does not affect the already-running process. Twice this
session, a code change (the `path`→`content` signature change, then the
base64 fix) had no visible effect until the stale `python.exe
mcp_server.py` process was found (`Get-CimInstance Win32_Process`) and
killed, letting Claude Code spawn a fresh one on the next tool call.
Reconnecting the MCP server from the client side did not, by itself, kill
and respawn the underlying process in this environment - only actually
terminating the process did.

## 7. Data lifecycle: what happens to `f` (received) and `f'` (returned)

Prompted directly: does the server retain files it receives, and does the
agent retain the decrypted result? Worth documenting precisely rather than
assuming, since the answer differs by side and by design intent.

### 7.1 Server-side: already ephemeral by design; one crash-recovery gap closed

Neither `mcp_server.py` nor `core.py` nor `document.py` ever write the
received `content` to disk - `document.load_abe()` calls `json.loads()`
on it directly, in memory. `server/abe.py`'s three OpenABE wrapper
functions (`keygen`, `decrypt_bytes`, `encrypt_bytes`) do write short-lived
temp files, because the underlying `oabe_*` CLI tools require file
input/output - but each already deleted its own temp files in a `finally`
block, on both success and failure, before this session started. So under
normal operation there was already nothing "received" sitting on disk for
any length of time.

The one real gap: if the server process is killed while a request is
in-flight (as happened twice this session, per §6.7 - by chance never
mid-request, but the risk is real), the `finally` block never runs and a
temp file could be orphaned. Closed by adding
`abe.cleanup_stale_temp_files(max_age_seconds=300)` - a sweep of
`tempfile.gettempdir()` for this module's own temp-file prefixes
(`abe_key_`, `abe_ct_`, `abe_pt_`, `abe_enc_`) older than the threshold,
called once at server startup (`mcp_server.py`), not per-request (the fast
path already cleans up synchronously; this is purely a crash-recovery
net). Verified directly: created a fake stale `abe_key_*` file with an
artificially old mtime, confirmed the sweep deletes it.

### 7.2 Client-side: a deliberate decision against persistent caching of `f'`

Asked whether the agent could retain `f'` (the decrypted result) locally,
to avoid re-sending `f` on repeated calls. This runs directly against the
project's central design principle, stated in `core.py`'s own module
docstring: every operation re-verifies the human's identity and the
agent's authorization **fresh, on every single call** - nothing is ever
treated as "already checked." A persistent client-side cache of `f'` would
quietly reintroduce exactly what that principle exists to prevent: if the
user's authorization changes after the cache is written (a Keycloak role
un-assigned, an attribute removed, a session revoked), the cached
plaintext would remain readable regardless, with no mechanism to notice
the change.

Three options were weighed explicitly: (a) no persistence at all - reuse
a result already visible in the current conversation, but re-fetch fresh
in any new session; (b) a local file with a short TTL (bounds the risk
without eliminating it); (c) a persistent, no-expiry local cache (maximum
convenience, but defeats the "never cache authorization" principle
entirely). **Decision: (a)** - no file is ever written for `f'`; reuse
within a single conversation is already free (it's simply still present in
context), and a new session re-derives it from scratch through the full
check every time.

### 7.3 Scratch-file hygiene: a `SessionEnd` hook

Testing the base64 transport fix (§6.5-6.6) involved writing several
throwaway files (base64-encoded ciphertext blobs, never plaintext) to the
session's scratchpad directory. Rather than rely on remembering to clean
these up, `.claude/settings.json` gained a `SessionEnd` hook (the event
that actually fires once at session end, as distinct from `Stop`, which
fires after every turn) that deletes any file named `pabel_scratch_*`
under this project's Claude Code temp-scratchpad tree - a convention
adopted going forward for any similar throwaway file. Verified directly:
created a dummy `pabel_scratch_test.txt`, ran the hook's exact command,
confirmed it was deleted.

## 8. Updated status (2026-07-30, end of session)

`read_document` now matches UC1's actual model (content handed over, not a
server-side path) on both transports, verified with a real decrypt against
alice's combined (user, agent) key. Two items remain genuinely open,
carried over unresolved from this section rather than from §5:

- The base64 payload-size limit (§6.6) - real, reproducible, root cause
  not yet isolated. Affects any single `read_document` call whose base64
  content exceeds roughly 10-12K characters.
- Everything listed at the end of §5 (the `realm-org.template.json` role
  declaration untested against a real Keycloak recreation; only one
  agent's compose service exists so far; the safest way to pick up a code
  change without recreating Keycloak/Postgres) remains open and unchanged.

## 9. A Claude Code plugin: the hook does the relay itself (2026-07-30, later session)

Prompted by "can this be packaged as a plugin for agents" - reasoned about
it agent-agnostically first: the MCP server (`server/`) is already a
standard MCP server, usable by any MCP client without change. What's
Claude-Code-specific is only the *connector* (`.mcp.json` + the
access-control hook), and a Claude Code **plugin** is the natural packaging
for that connector - not something that needs building for other agent
hosts yet, since none is targeted concretely (no speculative multi-host
code).

Two requirements, stated directly, changed the design materially from
§6.3-6.4's model:

1. **Target the deployed remote server** (`streamable-http`), never a local
   stdio stack - a plugin consumer's machine needs no Python venv,
   Postgres, Keycloak, or OpenABE binaries at all, which is exactly what
   Phase 2 was built to make possible.
2. **The client must "do nothing" - the hook itself must relay the file**,
   with **no exception limited to Read/Grep/Bash**: any tool that could
   touch an `.abe` path must be covered, and there must be no legitimate
   path except the hook sending it to the server.

### 9.1 Verified before designing around it, not assumed

Via targeted research against Claude Code's own docs (not guessed):

- A plugin is `<root>/.claude-plugin/plugin.json` + `.mcp.json` (same
  format as a project's own) + `hooks/hooks.json` (same shape as
  `settings.json`'s `hooks` key; `${CLAUDE_PLUGIN_ROOT}` resolves the
  plugin's own install path in hook commands). Distribution always goes
  through a `marketplace.json`, even for one local plugin.
- **The mechanism this whole design depends on**: a `PreToolUse` hook
  emitting `hookSpecificOutput: {permissionDecision: "deny",
  permissionDecisionReason, additionalContext}` both blocks the original
  tool call *and* delivers `additionalContext` into the **model's own
  context**, not just a UI message - confirmed against the hooks reference
  docs before writing any code around it.
- A remote MCP server is registered in `.mcp.json` via `"type": "http"`
  (or the `"streamable-http"` alias) + `"url"`, and **`.mcp.json` values
  support `${VAR}`/`${VAR:-default}` environment-variable substitution
  directly** - confirmed against the MCP docs. Claude Code's own MCP
  client handles OAuth for such a server itself (prompting the user to
  sign in via its `/mcp` panel on a `401`) - there is **no documented
  automatic flow from RFC 9728 protected-resource metadata alone**; the
  user still authenticates interactively the first time.

### 9.2 The relay hook (`claude-plugin/pabel/hooks/pabel_relay_hook.py`)

No tool-name matcher at all (fires for every tool - deliberately not an
enumerated allow-list). Detection runs over the entire serialized
`tool_input`, not specific known fields, so a tool this hook wasn't
written with in mind is still caught. Dispatch:

- `Bash` invoking an `oabe_*` binary directly → denied (defense-in-depth,
  ported from §6.4, for any dev machine that happens to have OpenABE
  locally - irrelevant for most plugin consumers, harmless either way).
- `Write`/`Edit`/`NotebookEdit` mentioning an `.abe`/`documents` path →
  denied outright, no relay attempted (this project has no
  authoring/write path at all - there is nothing legitimate to relay for a
  mutation).
- Anything else mentioning an `.abe`/`documents` path → look for **one
  concrete, existing file** among `tool_input`'s values; if found, the hook
  itself reads it, base64-encodes it, and calls the deployed server's
  `read_document` as its own MCP client (`pabel_client/relay.py`, using
  `mcp.client.streamable_http.streamablehttp_client` + `mcp.ClientSession`
  - the same `mcp==1.28.1` the server already pins), returning the result
  via `additionalContext`. If no single file can be identified (a
  directory, a glob pattern, several candidates, a Bash pipeline) → denied
  with no relay attempt, rather than guessing.

**A real bug found and fixed during testing**: the first version of the
file-detection regex extracted path-like substrings assuming no
whitespace, which silently fails on **this exact project's own path**
(`...\OneDrive - NTT DATA EMEAL\...` contains a space) - a Read call
targeting the real `documents/test.abe` came back "no concrete file
found," not because detection didn't fire, but because the extracted
candidate substring was truncated at the space and didn't exist on disk.
Fixed by checking whole structured-field string values (`file_path`,
`path`) as complete candidate paths *before* falling back to regex
substring extraction (only needed for free text like a Bash command),
plus a whole-quoted-segment check for a quoted path with spaces inside a
Bash command. **Lesson, stated generally**: test detection logic against
paths that actually look like the ones in play, not idealized
no-special-character examples - this exact bug would have shipped
undetected against a fixture living anywhere without a space in its path.

### 9.3 `list_documents` removed entirely

Raised directly: does the model really need a `list_documents` tool, given
the client now always initiates by handing the server a file it already
has? No - `list_documents()`/`DOCS_ROOT` (`server/core.py`,
`server/mcp_server.py`) only ever listed the server's own demo
`documents/` folder, a concept with no place once the client - not the
server - owns document discovery. Deleted outright, along with the now
fully-dead `DOCS_ROOT` (nothing else referenced it after §6.3 already
removed `resolve_within_root()`).

### 9.4 Verified end to end against the real deployed container

Rebuilding `mcp-server-claude-code` to test this session's earlier
`read_document(content, name)` changes (§6) surfaced one more operational
gotcha: **`nerdctl compose up -d <service>` does not recreate a container
just because its underlying image changed** - it reports "already
running" and does nothing. `nerdctl compose build <service>` alone doesn't
either. The change that actually worked, without touching Keycloak/Postgres
(checked directly via container creation timestamps before/after): `nerdctl
rm -f <container-name>` followed by a plain `nerdctl compose up -d
<service>` (no `--force-recreate`) - compose creates a fresh container from
the current image once the old one is gone, and never touches services it
merely `depends_on` if they're already running.

Against that freshly-built container, the full relay path was exercised
for real: `whoami`-equivalent identity resolution, then a `Read` attempt on
the actual `documents/test.abe` fixture (all three policy groups,
including the >12KB group that previously hit the §6.6 truncation bug) -
correctly returned `2/3` sections readable, matching alice's attributes
exactly as the direct-tool-call tests earlier this session had shown. The
previously-truncating group decrypted correctly this time, **because no
ciphertext or base64 text ever passes through the model's own output
tokens anymore** - the hook process handles the bytes directly. This
resolves §6.6 as a side effect of this redesign, for the hook-mediated path
specifically (a direct model-initiated `read_document` call, if one were
ever made instead, would still be subject to that limit). Also verified:
`Grep` and a quoted-path `Bash cat` on the same file relay correctly;
`Glob` on the `documents/` folder and `Edit` on the fixture are both denied
with no relay attempted, exactly as designed; an unrelated file passes
through with no hook output at all.

### 9.5 Known open items

- **Not verified**: whether this hook configuration also intercepts tool
  calls made from *within* a spawned subagent (the `Agent` tool). Hooks are
  assumed to operate at the tool-execution layer rather than
  per-agent-instance, but this was not tested against a real subagent call
  this session - avoid spawning subagents for `.abe`-adjacent work until
  confirmed.
- The hook's own `hooks.json` entry omits a `matcher` value entirely to
  mean "every tool" - this worked in direct piped-stdin testing of the
  script itself, but the *hook registration's* actual dispatch behavior
  (does Claude Code's harness really invoke it for every tool with no
  matcher present, versus some other default) has not been separately
  confirmed against a live installed-plugin session.
- Two independent OAuth sessions can exist for the same human: one Claude
  Code's own MCP client manages for direct `.mcp.json`-registered tool
  calls (`whoami`), one the relay hook manages itself
  (`pabel_client/session.py`, via `login.py`) for the relay mechanism. Not
  a security gap (the deployed server verifies either token identically),
  but a real, currently unavoidable UX rough edge - two logins instead of
  one.
- Bash free-text path detection (`find_relayable_file` in the relay hook)
  only reliably handles a bare unquoted path or one quoted with `"`/`'`;
  anything more complex (pipelines, multiple candidate paths in one
  command) is denied with no relay, by design (default-deny over guessing).

## 10. Phase 4 — agent-agnostic `pabel-connector`: one core, many adapters

Follow-up question after Phase 3 shipped: the Claude Code plugin proved the
"hook does the relay, model never touches ciphertext" pattern works - but
it's entirely Claude-Code-specific (its `hooks.json` schema, its
`additionalContext` mechanism). Could the same enforcement be offered on
other AI coding agents, packaged as one simple install? And could a
**Strategy pattern** structure it - one shared core, thin per-agent
adapters - rather than reimplementing detection/relay logic per agent?

### 10.1 Research: every popular agent's hook mechanism is different

Before designing anything, researched (web search, mid-2026) which coding
agents are most used and what each one's own hook/interception surface
actually supports - not assumed, checked per vendor's own docs. Full
matrix with citations: `connector/docs/coverage-matrix.md`. Headline
findings:

- **Claude Code** and **VS Code's native agent hooks** (Preview) share an
  *identical* JSON schema (`PreToolUse`, `hookSpecificOutput.
  {permissionDecision, permissionDecisionReason, additionalContext}`) - VS
  Code is even documented to auto-convert GitHub Copilot CLI's
  lowerCamelCase hook config into this same shape.
- **GitHub Copilot CLI** uses the same idea, but multiple open vendor
  issues (`github/copilot-cli#2585`, `#2980`) confirm `additionalContext`
  is not reliably delivered for `preToolUse` today - only the block itself
  (with its `permissionDecisionReason` text) is reliable.
- **Cursor** has three separate hook points (`beforeReadFile`,
  `beforeShellExecution`, `beforeMCPExecution`) instead of one generic
  event, each with its own `{permission, agentMessage, userMessage}`
  shape - and no pre-write-block hook at all (only post-hoc
  `afterFileEdit`).
- **Windsurf/Cascade** has four pre-hooks (`pre_read_code`,
  `pre_write_code`, `pre_run_command`, `pre_mcp_tool_use`) that block via
  **exit code 2** with **stderr** as the reason - not a JSON field at all,
  and whether it has any channel back into the model's own context (vs.
  only a human-visible log) couldn't be confirmed from docs.
- **Gemini CLI**'s `BeforeTool` (regex matcher, so `"*"` catches every
  tool) denies via `{"decision": "deny", "reason": ...}` - its
  context-injection hook (`BeforeAgent`) is a *different*, turn-scoped
  event, not wired to a specific blocked call.
- **OpenAI Codex CLI** hooks are opt-in and, critically, `PreToolUse`
  **only fires for the Bash tool** - Read/Write/Edit/MCP calls never reach
  a hook at all. A real, vendor-acknowledged limitation.
- **Cline**'s hooks are a JS/TS plugin SDK, and **explicitly
  macOS/Linux-only today - no Windows support**.
- **Continue.dev** has no pre-tool-use hook primitive at all - only a
  static allow/ask/disable policy.

A Plan subagent given this research plus the actual current code (not just
the request) surfaced two more facts that shaped the design:

1. **The server is one container per agent product** (`PABEL_AGENT_ID`
   baked in at deploy time) - so a server URL is never one global value
   once more than one agent is connected at once.
2. **A latent self-conflict bug** in the already-shipped Claude Code hook:
   detection scans the *entire* `tool_input` with no exemption for a
   direct, legitimate call to the `pabel` MCP server's own tools
   (`mcp__pabel__read_document`/`whoami`). A call passing `name="x.abe"`
   would match the `.abe` regex on that display-name argument and could be
   wrongly denied as ambiguous - the hook fighting its own sanctioned
   path. Never exercised by a test; fixed once, for every agent (§10.3).

### 10.2 New package: `PABEL/connector/` (`pabel-connector`)

```
connector/src/pabel_connector/
├── pabel_client/     # moved verbatim from claude-plugin/pabel/ - zero changes, already agent-agnostic
├── core/             # types.py, detection.py, decide.py - the ONE shared policy, no agent knows its name here
├── adapters/         # Strategy: per-agent parse(stdin)->NormalizedCall, render(Decision)->that agent's own shape
├── installers/        # Strategy: per-agent hook-config file discovery/read-merge-write
├── registry.py / installers/registry.py   # name -> Adapter / Installer, the dispatch tables
├── hook.py            # `pabel-connector-hook <key>`: the one runtime glue every agent's hook command invokes
└── cli/main.py        # `pabel-connector {list,install,uninstall,login,logout,doctor}`
```

`core/decide.py` is a direct generalization of the old
`pabel_relay_hook.py:main()` - same branches (oabe-binary check, mutating
tool check, ambiguous-file check, the relay call and its
`AuthError`/`RelayError` handling), with `NormalizedCall.is_write`/
`is_execute`/`mcp_target` replacing Claude-Code-specific field-name
assumptions, plus one new branch at the very top:

```python
if call.mcp_target and call.mcp_target[0] == "pabel":
    return Decision(DecisionKind.ALLOW)   # never fight our own sanctioned tool
```

closing the bug from §10.1.2 for every adapter at once, not per-agent.

### 10.3 Nine adapters built, at three different confidence levels

- **VERIFIED**: `claude-code` only - a behavior-identical port of the
  already-tested hook, re-confirmed byte-for-byte after the refactor
  (§10.5).
- **UNVERIFIED (built to vendor docs, no live install available)**:
  `vscode`, `copilot-cli`, `cursor` (3 hook points), `windsurf` (4 hook
  points), `gemini-cli`. Each folds the relay's decrypted content into
  whichever channel is actually documented as call-scoped and reliable for
  that vendor (`additionalContext` where confirmed; `permissionDecisionReason`
  / `reason` / `agentMessage` where the richer channel is unconfirmed or
  known-buggy) - a deliberate reliability fallback, not an oversight.
- **DEGRADED, UNVERIFIED**: `codex-cli` - Bash-only interception (a real
  vendor limitation), deny-only, no content-injection channel at all.
- **No adapter, documented gap**: `cline` (Windows-unsupported hooks),
  `continue-dev` (no hook primitive) - `docs/known-gaps.md`.

This tiering is enforced in the deliverables themselves, not just this
document: every adapter/installer module's own docstring states its
status, `registry.py`/`installers/registry.py` comment each entry, and the
connector's `README.md` has a coverage table - so "compiles and matches
the documented schema" is never presented as "confirmed working."

### 10.4 Packaging: one CLI, per-agent installers, never clobbering existing config

`pabel-connector install <agent> --dir <path>` (`cli/main.py`) looks up
`installers/registry.py`, calls that agent's `install()`, and prints the
env vars still needed (`PABEL_KEYCLOAK_*` shared,
`PABEL_SERVER_URL`/`PABEL_SERVER_URL__<AGENT>` per the one-container-per-
agent finding above). Each installer does a **read-merge-write** on that
agent's own hook-config file (`installers/base.py`'s `merge_hook_list`/
`read_json`/`write_json` helpers) - tested directly (not just asserted) to
preserve pre-existing, unrelated hook entries and to be idempotent across
repeated installs (`connector/tests/test_installers.py`). `uninstall`
reverses this via `remove_matching_commands`, matching only commands this
package itself wrote.

`claude-code`'s "installer" writes nothing - it prints the existing,
already-tested plugin's own `/plugin marketplace add`/`/plugin install`
steps, since that plugin already has real packaging benefits (versioning,
a bundled README) a hand-written hooks.json would just duplicate.

### 10.5 The Claude Code plugin: refactored onto the shared core, not frozen

`claude-plugin/pabel/pabel_client/` deleted (moved into
`connector/`); `hooks/pabel_relay_hook.py` shrank to:

```python
from pabel_connector.hook import main
if __name__ == "__main__":
    sys.exit(main(["claude-code"]))
```

`hooks.json`/`.mcp.json`/`plugin.json`/`marketplace.json`/`login.py`'s
user-facing behavior are unchanged - `requirements.txt` now depends on
`pabel-connector` (`-e ../../connector` for this same-repo checkout;
publishing it somewhere reachable without this checkout is an open
question, see `connector/README.md`'s "Distribution" section).

**Regression check performed** (no live OAuth login was available this
session to re-run the full decrypt-for-real path - see §10.6): piped the
exact same stdin payloads Claude Code would send into the *refactored*
script directly and confirmed byte-identical behavior to the pre-refactor
hook for every branch that doesn't require a live token - unrelated file
(silent allow), `Edit` on a real `.abe` fixture (`DENY_MUTATING`), `Glob`
on the `documents/` folder (`DENY_AMBIGUOUS`), a direct
`mcp__pabel__whoami` call (silent allow - proving the §10.1.2 bug fix
works through the real CLI wiring, not just the importable function), and
a `Bash cat` of the real fixture with `PABEL_SERVER_URL` set (correctly
reached the auth-check stage and failed with "not logged in," proving
detection → file-finding → relay-dispatch all wire through correctly, with
only the final "does the server actually decrypt" step - unchanged,
already-proven server code - left unexercised this session).

### 10.6 What's still open

- **Only Claude Code has been tried against a real, live install.** VS
  Code Copilot Chat needs a paid subscription not available this session;
  Cursor/Windsurf/Copilot CLI/Gemini CLI/Codex CLI were not installed
  locally at all. The user explicitly chose to build all adapters now
  anyway (rather than wait), planning to have a tutor/colleagues try the
  rest later - every UNVERIFIED/DEGRADED label in this package exists
  specifically so that handoff doesn't get mistaken for "done."
- Windsurf is the single most important one to check first: whether it
  delivers the relayed content back to the model at all (vs. only into a
  human-visible log) is completely unconfirmed, and the whole point of
  this package is the invisible-relay UX, not just blocking.
- Cursor/Windsurf/Gemini CLI/Copilot CLI installer config file
  *locations* are a mix of confirmed (Cursor, Windsurf, Gemini CLI) and
  best-guess (VS Code, Copilot CLI, Codex CLI) - see
  `connector/docs/coverage-matrix.md` for which is which.
- `pabel-connector` itself isn't published anywhere yet - `pip install -e
  .` from a repo checkout is the only install path today; an internal
  index or pinned git URL is needed before a real employee rollout.

## 11. Closing a real inconsistency: this repo's own dev hook didn't match its own design

Directly challenged: the original requirement (§9's framing - "the client
must do nothing, the hook blocks everything except sending it to the
server") was never actually true for *this repository's own* Claude Code
session. `.claude/hooks/block_abe_direct_read.py` (§6.4's deliberate
loosening, correct for the architecture at the time) only ever blocked a
direct `oabe_*` CLI invocation - Read/Grep/Bash-cat on a raw `.abe` file
went straight through, unlike the plugin (§9), which enforces the full
policy. That gap had simply never been revisited once §9-10 changed the
actual mechanism (the hook does the relay itself, so the model no longer
needs to read raw ciphertext directly at all, even in this dev repo).

**Fix**: `.claude/hooks/block_abe_direct_read.py` replaced with
`.claude/hooks/pabel_relay_hook.py` - the same ~10-line dispatch into
`pabel_connector.hook.main(["claude-code"])` the plugin itself uses (not
a second, parallel implementation). `.claude/settings.json`'s
`PreToolUse` hook lost its `"matcher": "Bash"` restriction (now catch-all,
matching the plugin's `hooks.json`) and gained an `env` block
(`PABEL_SERVER_URL`, `PABEL_KEYCLOAK_*`) so the relay has a server to call.
The hook command itself uses `$CLAUDE_PROJECT_DIR`-relative paths (already
an established pattern in this exact file) rather than a hardcoded
personal absolute path, so the committed `settings.json` stays portable.

**A second, independent bug found while verifying the fix**: writing this
very documentation - prose that discusses `documents/Test.abe` - was
itself denied as `DENY_MUTATING`, as if it were an attempt to overwrite an
encrypted file. Root cause: the shared core's mutating-tool check scanned
the *entire* tool-call payload for an `.abe`/`documents/` mention, never
distinguishing "the write's target is one" from "the write's content
merely discusses one." Fixed by adding `NormalizedCall.write_target` (the
specific path a write actually targets, populated per-adapter) and having
`DENY_MUTATING` check only that field - `connector/src/pabel_connector/
core/types.py`/`decide.py`, propagated to every adapter that models a
write (`claude_code`, `vscode`, `copilot_cli`, `windsurf.pre_write_code`,
`gemini_cli`; `cursor` has no write hook at all, `codex_cli` never sets
`is_write`). Two regression tests added
(`connector/tests/test_decide.py`) - a write whose *content* mentions an
`.abe` path is now allowed; a write whose *target* is one is still denied.

**Bootstrapping problem worth naming**: fixing the core while the
old-but-not-yet-fixed hook was still active meant the fix itself couldn't
be written - editing `decide.py` to *mention* `.abe` paths in a comment
triggered the very bug being fixed. Worked around by temporarily removing
`.claude/settings.json`'s `PreToolUse` array (a diff containing no `.abe`
mention, so it wasn't itself blocked), making the fix, then restoring it -
a real, if narrow, example of a security control blocking its own
maintenance, worth remembering if it recurs.

**Verified**, both via the pytest suite (53 tests, up from 50) and by
piping real payloads through the actual script: `Read`/`Grep`/`Bash cat`
on the fixture now all deny and attempt the relay (stopped at "not logged
in" - no live session this session, same wall §10.5 hit); `Glob`/`Edit`
still deny as before; an unrelated file and a direct `mcp__pabel__whoami`
call still pass through silently; and - the specific regression - writing
a test-payload file whose *content* mentions `Test.abe` succeeded without
incident. Full before/after results:
`docs/access-methods-test.md` (superseded) and
`docs/access-methods-test-after-fix.md` (current).

**Still open, same as §10.6**: no interactive Keycloak login was
performed this session, so every relay attempt above stopped at "not
authenticated" rather than proving a real decrypt - `connector/docs/
verification-procedure.md` (new this session) is the structured checklist
for closing that out, for this repo and for every other UNVERIFIED
adapter, so results from different testers stay comparable.

## 12. Reversing "one container per agent": real, per-installation agent authentication (2026-07-31, later session)

### 12.1 What was wrong, and how it was found

A document written to explain the whole system in plain terms (for the
project owner, not this log) described §4.3/§10's design accurately - and
that plain description is what exposed the problem: "each agent product
runs as its own container, with `PABEL_AGENT_ID` fixed at deploy time."
Said out loud, this is just *"the server trusts whichever container/URL
you happened to reach"* - there was never a per-request cryptographic
proof of agent identity at all, only a deployment-topology assumption. The
project owner rejected this outright, on two counts: it isn't actually
agent-*agnostic* if a separate server exists per agent (the whole point of
Phase 4's connector), and an agent's identity must be authenticated, not
inferred - *"non possiamo fidarci solo dello 'user agent'"* (we can't just
trust "the user agent").

A first proposal answered "authenticate the agent" with a self-service
model: `pabel-connector install <agent>` would call a network-reachable
`/enroll` endpoint on the server, which would itself hold Keycloak-admin
credentials to mint a new client on request. The project owner rejected
this shape specifically: *"perché dovresti volere un account admin? Se
intendi che il plugin debba installarlo, sbagli. Assumi che ogni account
venga creato dall'azienda"* (why would you want an admin account? If you
mean the plugin should install it, you're wrong - assume every account is
created by the company). This is the same principle already on file from
an earlier round of corrections (§0/project memory: *"Soltanto gli admin
devono essere in grado di aggiungere attributi"* - only an admin may add
attributes) applied to a new kind of "attribute": creating a credential
that lets something act as a registered agent is exactly as privileged an
operation as setting a user's ABE attributes, and just as exclusively
admin-only. The fix wasn't a smaller network surface for `/enroll` - it
was deleting the idea of a network-reachable enrollment path entirely.

### 12.2 Why this isn't the "Keycloak client per agent" already rejected in §4.2

The new design also gives each agent a Keycloak client - which sounds, on
a skim, like re-proposing §4.2 (rejected for two concrete reasons: Claude
Code's own MCP OAuth client_id is Dynamic-Client-Registration-generated,
not a stable admin-chosen string; and how Claude Code isolates OAuth state
across several configured remote MCP servers was undocumented). Neither
objection actually applies here, because this is a structurally different
proposal, not the same one revisited:

- §4.2 wanted the agent's identity to ride inside the **same** OAuth
  connection Claude Code's own MCP client establishes to talk to the
  server - i.e. let whatever `client_id` Claude Code's MCP client ends up
  using **be** the agent identity. That's what made Claude Code's own DCR
  behavior and multi-server state isolation load-bearing.
- This design's agent credential is obtained through a **wholly separate**
  `client_credentials` grant, called directly by the connector's own code
  (`pabel_client/keycloak_client.py:client_credentials()`) against
  Keycloak's token endpoint - exactly the same pattern this project
  already uses for the human's own browser login
  (`pabel_client/oauth_browser.py`) and every relay call: PABEL's own code
  talks to Keycloak directly, never through whatever OAuth handling an
  agent's built-in MCP client applies to its own tool-registration
  connection. Claude Code's MCP client is never involved in obtaining or
  presenting this token at all.
- The client itself is never self-registered (no DCR): an admin creates
  it explicitly (`agents_admin.py create-installation`), so there's no
  unpredictable, Keycloak-generated identifier to key a lookup table on -
  the admin controls and records the mapping (`agent_installations`)
  directly.
- There is now only **one** MCP server URL, shared by every agent -
  "isolating OAuth state across several configured remote servers" is
  moot when there's only one server to configure in the first place.

### 12.3 Design: per-installation `client_credentials`, provisioned admin-only, never over the network

- **One shared server** (`server/compose.yml`'s `mcp-server-claude-code`
  service collapses to a single `mcp-server`; `PABEL_AGENT_ID` removed
  from `mcp_server.py`, `.env.example`, `Dockerfile` entirely).
- **Every agent installation is its own Keycloak confidential client with
  a service account** (`client_credentials` grant only - no browser flow,
  no direct password grant). An admin creates one per employee/machine:
  `python agents_admin.py create-installation claude-code --label "..."`,
  which calls the Keycloak Admin REST API using the exact same
  bootstrap-admin pattern `setup_user_profile.py` already established
  (password grant against `admin-cli` in the `master` realm, then
  authenticated calls to `/admin/realms/{realm}/...`), prints
  `client_id`/`client_secret` **once**, and records the mapping in a new
  `agent_installations` table (`schema.sql`) - `client_id` (the real
  Keycloak identity) → `agent_id` (the product, e.g. `"claude-code"`,
  unchanged in meaning from §4.3's `agents` table). Nothing here is ever
  reachable over a network from an employee's own machine - the employee
  only ever *receives* an already-created credential out of band and
  stores it locally (`pabel-connector install <agent> --client-id ...
  --client-secret ...`, or `claude-plugin/pabel/enroll.py` for the Claude
  Code plugin specifically) - `agent_session.py`'s `store_credentials()`
  is a pure local write, never a call to the PABEL server.
- **`core.resolve_agent()` is rewritten** from `resolve_agent(agent_id:
  str, user_roles)` (trusted input) to `resolve_agent(agent_token: str,
  user_roles)` (a credential that must prove itself): verifies the token
  exactly like the human's (`auth.py`'s `KeycloakAuth.verify()` -
  signature/issuer/expiry against Keycloak's JWKS), extracts its `azp`
  claim (`KeycloakAuth.client_id_of()`, new) as the cryptographically
  verified installation identity, resolves it through
  `agent_installations` to find which product it belongs to, then applies
  the *same* two-failure-mode logic §4.3 already established (unknown/
  revoked installation or disabled product → hard `AuthError`; known
  installation whose product's `required_role` the current user's token
  lacks → soft `""`, unchanged). `whoami`/`read_document` (`mcp_server.py`)
  both gain a required `agent_token: str` parameter as the only channel
  for this credential - MCP's own bearer-auth middleware
  (`token_verifier.py`) still covers exactly one connection-level
  credential (the human's), unchanged, so the agent's has to travel as an
  explicit tool argument instead.
- **Revocation is per-installation, not per-product**: `agents_admin.py
  revoke-installation CLIENT_ID` flips `agent_installations.revoked` (the
  fast, authoritative check `resolve_agent()` runs) and best-effort
  disables the Keycloak client too (defense in depth - an already-issued,
  unexpired token is unaffected either way, same residual window as any
  OAuth revocation). Because every row is keyed by `client_id`
  independently, revoking one compromised laptop can't touch any other
  installation of the same agent product.

### 12.4 Connector side: a second, per-product-keyed credential store

New module `pabel_client/agent_session.py`, deliberately separate from
`session.py` (the human's session): different lifecycle (a credential
obtained once from an admin, not repeated interactive login) and, more
importantly, **keyed per agent product** (`agent_credentials.json`:
`{"claude-code": {...}, "cursor": {...}}`) since one machine can run
several enforced agents side by side, each its own enrolled installation.
`relay.py`'s `read_document(path, name, agent_id)` now fetches and attaches
both credentials to every call. Propagating *which* installation's
credential to use turned out to touch three files, not one - `hook.py`
derives it from the dispatch key it already receives
(`agent_id = key.split(":", 1)[0]`, consistent with `registry.py`'s
existing `<agent>:<hookpoint>` convention for multi-hook-point agents),
`core/decide.py`'s signature becomes `decide(call, agent_id)`, and
`relay.py` as above - the seven adapter modules themselves need zero
changes, confirmed by inspection: none of them imports or calls anything
from `pabel_client` or agent identity at all, they only ever produce a
`NormalizedCall`/consume a `Decision`.

### 12.5 A second-order consequence: direct model calls needed a real fix, not just a passthrough

§10.1's own-tool allowlist (`if call.mcp_target[0] == "pabel": return
ALLOW`) let a model call `whoami`/`read_document` directly - useful, since
`whoami` is meant partly as self-diagnostics ("why did this section come
back denied"). Requiring `agent_token` on every call breaks that
silently: a model has no legitimate way to hold this installation's own
credential, so a direct call would just fail. Asked explicitly, the
project owner chose to keep it working rather than accept the regression:
the hook itself (which already intercepts every tool call, including this
one) injects the credential on the model's behalf before allowing the
call through, via `hookSpecificOutput.updatedInput` - confirmed part of
Claude Code's actual `PreToolUse` schema. `Decision` gained an
`updated_input` field for this; `decide()`'s `mcp_target` branch now looks
up this installation's token (`agent_session.access_token(agent_id)`) and
returns it merged into the original `tool_input`, and
`adapters/claude_code.py` is - for now - the only adapter whose `render()`
acts on it. Every other adapter ignores the field and allows the call
unmodified; the server then rejects the missing/invalid `agent_token` with
a clean error. That's a **safe fallback, not a hole**: the model still
never sees a real credential either way, it just doesn't get the
self-diagnostic convenience yet on agents whose hook schema can't rewrite
tool input - worth revisiting per-adapter as each one is actually verified
(`connector/docs/verification-procedure.md`).

### 12.6 Status

Unit tests: `server/tests/` is new this session (the server had none before
- `test_resolve_agent.py`, `test_auth_client_id_of.py`, monkeypatch style
matching `connector/tests/`, no live Keycloak/Postgres required to run
them); `connector/tests/` grew to cover the injection branch, the
`agent_id` parameter threaded through `decide()`, and the new
`agent_session.py` module (72 tests total across both packages, all
passing).

**Verified live**, against the actual running Keycloak + Postgres
(`nerdctl compose ps` showed both already up from earlier sessions):
`agents_admin.py create-installation claude-code` really creates a
confidential client + service account via the Keycloak Admin REST API and
records it in `agent_installations`; a genuine `client_credentials` grant
against that client produces a token `core.resolve_agent()` verifies and
resolves correctly - `("claude-code", "")` with no matching role passed,
`("claude-code", "agent_claude_code")` with `agent_claude_code_user`
passed, matching §4.3's soft/hard split exactly. A garbage token is
rejected (`AuthError: ... invalid token: Not enough segments`); revoking
the installation (`agents_admin.py revoke-installation`) makes the *same*,
still-cryptographically-valid token rejected on its very next call
(`unrecognized or revoked agent installation`) - confirmed the Postgres
check, not just token expiry, is what actually gates access. On the
connector side, `pabel-connector install claude-code --client-id ...
--client-secret ...` stored credentials in `~/.pabel/agent_credentials.json`
for real, and `agent_session.access_token("claude-code")` obtained a real
token through it independently of the check above - end to end, admin
provisioning through connector-side use, without touching any mocks.

**Still open**: no full MCP round trip (a real relay through
`read_document` over `streamable-http`, or the transparent
`updated_input` injection inside an actual Claude Code session) was
exercised, because both still need an interactive human browser+MFA login
this session never performed - the same wall §10.6/§11 kept hitting.
Everything upstream of that single missing piece (agent-side
authentication, specifically) is now confirmed working against real
infrastructure, not just unit-tested against mocks.

## 13. Two bugs found live-testing a non-Claude-Code agent for the first time (2026-07-31, later session)

Trying to log in through an agent other than Claude Code (VS Code Copilot)
was the first time this project actually drove `pabel-connector login`
end to end rather than the plugin's own `login.py` - it surfaced two real
bugs immediately, both latent since earlier in Phase 5 and invisible until
something other than Claude Code's own path was exercised.

### 13.1 `oauth_browser.py`'s connector copy used the wrong callback port

Keycloak rejected the login with "Invalid parameter: redirect_uri", and
the URL it was given used port 8767. The `pabel` client's *only*
registered `redirectUris` entry (`realm-org.template.json`) is
`http://127.0.0.1:8766/callback` - one client, one redirect URI, shared by
every login path (`server/oauth_browser.py`, used by `server/login.py`/
`core.py`'s dev login; and the connector's own ported copy,
`connector/src/pabel_connector/pabel_client/oauth_browser.py`, used by
`pabel-connector login` and, transitively, `claude-plugin/pabel/login.py`).
The connector's copy defaulted `CALLBACK_PORT` to `8767` instead of `8766`
- an unexplained divergence introduced when the file was ported (its own
docstring says "same flow, same reasoning" as the server's, giving no
reason for a different port, because there wasn't one). Nothing in
`docs/` or the realm config ever registered a second redirect URI, so this
was always a bug, not an intentional second port - it just went unnoticed
because Claude Code's own verified-working session predated this file, or
because `PABEL_CALLBACK_PORT` happened to be set by hand during whatever
testing did pass. `.cursor/hooks/pabel_session_init.py` already injected
`PABEL_CALLBACK_PORT=8766` into Cursor's own hook-subprocess environment -
apparently an earlier, undocumented workaround for this exact mismatch -
but that env var only reaches Cursor's *hook* subprocesses
(`pabel_connector.hook cursor:...`), never a manually-run
`pabel-connector login`, so it never actually fixed the login path for
anyone. Fixed at the root: the connector's default is now `8766`, matching
the server's and the one registered redirect URI. The Cursor workaround
was left in place (harmless now that it matches the corrected default) but
is no longer load-bearing.

### 13.2 The containerized server's `audit.jsonl` was never reaching the host

Separately: nothing from the containerized `mcp-server` (the actual
shared, `streamable-http` deployment every non-Claude-Code agent relays
through) was ever appearing in `server/audit.jsonl`, regardless of the
above. `core.py`'s `AUDIT_LOG` was `Path(__file__).resolve().parent /
"audit.jsonl"` - unconditionally relative to the module's own location.
Inside the container that's `/app` (`Dockerfile`'s `WORKDIR`), and
`compose.yml`'s `mcp-server` service never mounted anything back to
`/app/audit.jsonl` - every entry a containerized run ever wrote landed in
that container's own throwaway filesystem and was lost on the next
`compose up`. The `server/audit.jsonl` visible on the host the whole time
was exclusively from local, non-containerized runs (stdio mode, dev
scripts) - a different file that happened to share a name and a directory,
not a partial view of the same trail. This is a real gap in the
accountability guarantee §0/the project README leads with ("who or what
did this must be answerable from the log alone") for the one deployment
topology - a single shared remote container - the project is actually
meant to validate.

Fixed by making the path configurable (`core.py`'s `AUDIT_LOG` now reads
`PABEL_AUDIT_LOG_PATH`, falling back to today's behavior when unset - every
non-container caller is unaffected) and giving `mcp-server` a real host
mount for it: a new `./state:/app/state` volume plus
`PABEL_AUDIT_LOG_PATH=/app/state/audit.jsonl`, landing at
`server/state/audit.jsonl` on the host. A directory mount rather than a
single-file one deliberately, so a fresh clone with no pre-existing audit
file doesn't hit the classic bind-mount gotcha (a missing single-file
source silently becoming a directory in both places).

Neither bug involved the agent's own hook config at all - both sat
strictly between "human logs in" and "a request from any agent reaches the
server," so they would have blocked *every* agent equally, Claude Code
included, the moment its login stopped going through a path that happened
to dodge them. Still separately true and unrelated to either bug: VS Code
Copilot itself had no PABEL wiring in this repo at all yet
(`.vscode/hooks.json` didn't exist anywhere under the project) -
`installers/vscode.py`'s own status is UNVERIFIED with an explicitly
unconfirmed hook-file path/schema (§ coverage-matrix.md), so actually
running `pabel-connector install vscode` is the next real test, and it may
surface a third, genuinely VS-Code-specific gap rather than a shared one.

### 13.3 A third bug, found trying to bring the real `mcp-server` up: a 25-hour-old container was squatting on its port, still running the pre-§12 trust model

`nerdctl compose up -d mcp-server` (the actual fix verification step for
§13.2) failed: `port is already allocated` on 8001. `nerdctl ps -a` showed
why - `server-mcp-server-claude-code-1`, image
`server-mcp-server-claude-code:latest`, **created 25 hours earlier** and
still `Up`. This was the container from before this session's rename
(§ above: `mcp-server-claude-code` → `mcp-server`, one shared service) -
`nerdctl compose down`/renaming the service in `compose.yml` does not stop
or remove a container compose no longer knows the name of, so it had been
running untouched, on the one port every agent's `PABEL_SERVER_URL` points
at, through this entire session's §12 work.

Checked its actual environment (`nerdctl inspect ... Config.Env`):
`PABEL_AGENT_ID=claude-code`, baked in at that old image's build/run time -
the literal env var §12 exists to delete. Any agent that successfully
reached `localhost:8001` at any point up to this point in the session -
this includes whatever prompted the project owner to ask *"perché
l'agente di vscode [dovrebbe impersonare] claude code e non usare il suo
[...] id"* (why would the vscode agent impersonate claude code instead of
using its own id) - was answered by this container, which has no
`resolve_agent()`, no Keycloak `client_credentials` check, nothing: it
trusted the fixed env var unconditionally, exactly like every container
did before §12. The confusion had a literal, physical cause, not a vscode
adapter defect: a leftover process from the design already being replaced
was still the thing actually listening. Removed (`nerdctl rm -f`); the
real `mcp-server` (image `server-mcp-server`, no `PABEL_AGENT_ID` anywhere
in it) now holds 8001 instead.

**A fourth, self-inflicted issue surfaced immediately after**, from the
same `up -d mcp-server` call: nerdctl recreated `keycloak` and `postgres`
too, not just `mcp-server` - apparently nerdctl compose recomputes a
config-hash and reconciles every service whenever `compose.yml` changes at
all, not just the service actually touched (unconfirmed whether this is
nerdctl-specific behavior or shared with `docker compose`; not
investigated further, just now known to happen). Postgres's data survived
(`server_pgdata` is a named volume, untouched by container recreation).
Keycloak's did not: `start-dev --import-realm` only ever repopulates what
`realm-org.json` describes (the three demo users, the `pabel` client,
fixed realm roles) - anything created afterward through the live Admin
API, i.e. every Keycloak client `agents_admin.py create-installation` had
ever made, was gone. Confirmed directly (`GET
.../clients?clientId=...` for each): both of this machine's
until-then-"active" installations (`claude-code`, `cursor` -
`pabel-agent-claude-code-08cf6c47f9e0aaa3` and
`pabel-agent-cursor-4fd67f81c52ac96f`) had a live Postgres row pointing at
a Keycloak client that no longer existed - not a hypothetical failure
mode, a real one, on this exact machine, at this exact moment. No human
session/MFA state was actually lost, since (§12, "Still open") one had
never yet been established.

Fixed using the project's own designed recovery path, nothing ad hoc:
`revoke-installation` on both dead rows (Postgres now honestly reflects
"unusable" instead of silently lying "active"; the tool's own
best-effort Keycloak-side disable predictably no-oped with a clear warning,
since that client was already gone - exactly the degraded-but-safe
behavior `revoke-installation` was written for), then
`create-installation` for fresh `claude-code`/`cursor` credentials, then
`pabel-connector install <agent> --client-id ... --client-secret ...` to
overwrite the dead entries in this machine's own
`~/.pabel/agent_credentials.json`. Re-verified **against the live,
post-recreation stack** rather than assumed fixed: both new installations
obtained a real `client_credentials` token, and each token round-tripped
through the real `core.resolve_agent()` (not a mock) to the expected
`(agent_id, "")` - the same soft, no-role-granted outcome §12's live
verification already exercised, now reproduced from scratch. `nerdctl
images`/`network ls` also had two stale images
(`server-mcp-server-claude-code`, and an older, differently-tagged
`pabel-mcp-server:test` from even earlier manual testing, both confirmed
unused by any container) and one orphaned network (`service_default`, zero
attached containers, name predating this directory being called `server/`)
- all removed. `nerdctl ps -a`/`images`/`volume ls`/`network ls` now show
exactly what `compose.yml` currently describes and nothing else.

Net effect of this whole detour: the project's actual, shared,
`resolve_agent()`-checked `mcp-server` had - despite §12 believing itself
"verified live" - never actually been reachable on its own advertised
port even once before today, because something else had always answered
first.

## 14. "Could an agent just lie about who it is?" - two concrete hardenings, prompted by §13.3

The project owner's reaction to §13.3, reasonably: if a leftover container
could silently mislabel every request as claude-code, what actually stops
an agent from "lying" more generally, and shouldn't every adapter be
*forced* through its own specific Keycloak login rather than trusted to
do the right thing on its own? Worth being precise about what was already
true versus what genuinely wasn't, rather than assuming either "already
fine" or "wide open."

**Already true, re-confirmed by re-reading `decide.py`/`agent_session.py`
line by line rather than from memory**: an agent product never performs
its own Keycloak login and never gets to pick which credential is used.
`agent_id` (from `hook.py`, `key.split(":", 1)[0]`) is a fixed string
baked into that specific installer's own hook-config command at install
time - not something the agent supplies at request time. Every path that
talks to the PABEL server (the relay in `read_document`, and the
direct-call injection branch) unconditionally calls this installation's
own `agent_session.access_token(agent_id)`, which performs a real
`client_credentials` grant against Keycloak using only the specific
secret an admin provisioned for that exact installation. `decide.py`'s own
docstring already stated the intended model precisely: `agent_id` is "a
local key selecting which of this installation's own stored credentials
to use... never a value sent anywhere as a claimed identity" - the server
only ever trusts the verified token's `azp` claim (`resolve_agent()`).
None of this can be skipped or substituted by the agent product itself,
because the agent product's own code never runs any part of it.

**What genuinely wasn't covered, and is the real shape of the §13.3
incident**: when *no hook is wired at all* for a given agent (exactly
vscode's situation - no `.vscode/hooks.json` existed in this project),
none of the above runs, at all - there's no "wrong credential" to catch
because our code isn't in the loop to begin with. A stored credential
with a broken/missing/reverted hook is invisible: nothing surfaced it
until §13.3 was investigated by hand. Separately, and unrelated to hook
wiring: this installation's own local secrets
(`~/.pabel/session.json`, `~/.pabel/agent_credentials.json`) were never
protected from being read directly by the model like any other file -
`decide()`'s existing checks only ever look for `.abe`/`documents/`
mentions, so a plain `Read`/`Bash cat` on either file would have sailed
through unremarked, handing a live, usable credential straight into the
model's own context (a far more direct route to "acting as that agent"
than anything about hook wiring).

**Fixed, both scoped and shipped this session**:

1. **`pabel-connector doctor` now checks hook wiring, not just that a
   credential exists.** For every agent with stored credentials
   (`agent_session.installations()`), a new `_hook_wiring_ok()`
   (`cli/main.py`) resolves that installer's `config_path`/`HOOK_KEYS`
   (skipped for installers with no config file of their own, e.g.
   claude-code) and checks the expected `pabel-connector-hook` command(s)
   are actually present in the file, not merely that the file exists.
   `doctor` gained a `--dir` option to know where to look (matching
   `install`/`uninstall`). Verified live by staging exactly the vscode
   scenario - a stored credential, `--dir` pointed at this repo, no
   `.vscode/hooks.json` - and confirming doctor reports precisely: *"hook
   config at [...] is missing or doesn't call the pabel hook (1/1 hook
   point(s) not wired) - nothing will actually enforce PABEL for 'vscode'
   until this is fixed."* Test entry removed afterward, real `doctor`
   output confirmed clean again for `claude-code`/`cursor`.

2. **Reading PABEL's own local credential files is now denied
   unconditionally**, before any other check in `decide()` runs.
   `detection.py` gained `touches_pabel_credential_store()`, resolving
   the *actual* `session.SESSION_FILE`/`agent_session.CREDENTIALS_FILE`
   paths (respects `PABEL_PLUGIN_DATA_DIR`, not a guessed path) and
   walking `tool_input`'s real string values for them - deliberately not
   `json.dumps()`-then-regex like `mentions_target()`, since JSON-encoding
   doubles backslashes and would never match a raw Windows path (found
   via a failing test before the fix landed). New `DecisionKind.
   DENY_CREDENTIAL_ACCESS`; every adapter already treats "anything but
   ALLOW" as a generic deny using `decision.reason`, so zero adapter
   changes were needed. **Verified live, not just by its own test suite**:
   this repo's own Claude Code session (i.e., this very agent) attempted a
   `Read` on `agent_credentials.json` for unrelated cleanup and was
   denied by its own hook with exactly this message - about as direct a
   confirmation as this kind of check can get.

10 new tests (`connector/tests/test_detection.py`,
`test_decide.py`, new `test_cli_doctor.py`) - 73 connector tests total, up
from 63, all passing.

**Still not "prevented" in an absolute sense, worth naming rather than
implying otherwise**: a hookless agent's own native MCP connection can
still reach the PABEL server directly and would still need a genuinely
valid `agent_token` the server verifies - nothing here changes that this
was already sound. What changed is closing the two concrete ways an
*installed* credential's protection could silently not apply: an
uninstalled/broken hook (now loud in `doctor`), and this installation's
own secrets being as readable as any other file (now denied outright).
Re-opening whether an agent's *own* native OAuth connection could itself
carry a verified per-product identity is the already-rejected §4.2/§12.2
idea (blocked by Claude Code's DCR-generated client_id and, likely, most
other agents' equivalent) - not reopened here.

## 15. A third hardening (hook-bypass detection), then a live vscode test that broke everything open

### 15.1 Closing the bypass §14 didn't cover: direct invocation of this package's own internals

§14 closed "reading credential files directly" and "no hook installed at
all is invisible." A third, sharper version of the same worry (project
owner: *"non dovremmo assumere che facciano di testa loro"* - we shouldn't
assume they'll do their own thing) turned up in a saved transcript
(`GitHub Copilot.md`, predating this section's fix): with no vscode hook
wired yet, Copilot ran `relay.read_document('documents/Test.abe',
'Test.abe', 'claude-code')` directly from a Bash one-liner - not a
cryptographic forgery (the server truthfully attributed the read to
claude-code, the credential that was actually presented), but a real
audit-trail misattribution, and exactly the kind of "improvise past the
enforcement" behavior an agent with unrestricted code execution can always
attempt.

The project owner's own follow-up question resolved the obvious objection
before it needed asking: doesn't blocking this risk false positives in
this repo, where `pabel_connector` internals are invoked constantly as
routine development? The answer is structural, not behavioral: the hook's
own legitimate invocation of these functions never appears as a
`tool_input` `decide()` evaluates *at all* - hook.py's own process *is*
the evaluator, not something being evaluated. Only a model's own
Bash/terminal tool call asking to import these modules directly is
ever a `tool_input` in the first place, and a model has no legitimate
reason to construct one in a normal downstream project. The remaining
question was how to tell "this repo, developing the connector" apart from
"a project that merely depends on it" - resolved by asking about the
*project* (does `connector/src/pabel_connector/core/decide.py` exist
relative to cwd - i.e. is this literally the PABEL monorepo), never the
*installed package* (an editable `pip install -e connector` - this
project's own current, documented install method - would resolve to this
same source path even from a totally unrelated downstream project, making
that signal useless).

Added: `detection.py`'s `invokes_pabel_connector_internals()` +
`_is_pabel_connector_source_checkout()`, a new `DecisionKind.
DENY_HOOK_BYPASS`, checked in `decide()` right after the credential-store
check, scoped to `is_execute` calls only (a `Write`/`Edit` merely
mentioning the package name - e.g. in a requirements.txt - is not itself
dangerous; running code is). Verified live in the most direct way
available twice over: a synthetic test reproducing the exact transcript
command, and this repo's own Claude Code session (i.e. this agent,
mid-conversation) confirmed unaffected running its own routine `doctor`/
`agents_admin.py` commands throughout.

### 15.2 The real vscode live test: nothing fired, root cause was the install location itself

Following up on the credential/role setup needed to actually try vscode
live (registering the `vscode` agent product, creating an installation,
discovering along the way that Keycloak's dev-mode durability gap - §2
point 5 - had also silently dropped `cursor`'s realm role across the
container-recreation incident in §13.3, fixed by recreating both roles),
a real VS Code Copilot session was pointed at the repo and asked to read
`documents/Test.abe`. Saved transcript: `vs_code_chat_2` (test directory).
Copilot read the file completely unobstructed via a raw terminal command -
the hook never fired at all, and when told directly to use it, Copilot
could not explain how, eventually just repeating meta-commentary about
"checking the real channel" without ever producing a blocked/relayed
result. **That itself was the tell**: in a working setup (Claude Code),
the model never needs to be told to "use the hook" - it just attempts a
normal read and is transparently redirected. Needing to ask at all meant
the hook had not fired even once.

Root cause, found by re-checking code.visualstudio.com's current docs
rather than trusting the original (2026-07) research: `installers/
vscode.py` wrote `.vscode/hooks.json` - **a path VS Code's real "agent
hooks (Preview)" feature never reads at all**. The confirmed
workspace-scope location is `.github/hooks/*.json`. This was not a schema
mismatch VS Code tolerated or partially handled - it was a file VS Code
never looked at, so literally nothing in this package's design was ever
exercised in that live session, regardless of anything else being right
or wrong. The JSON shape written was also wrong independently (Claude
Code's nested `[{"hooks": [...]}]` wrapper; VS Code's confirmed native
shape is a flat array directly under the event key) and the adapter's
tool-name assumptions were also wrong (real names are `editFiles`/
`createFile`/`deleteFile`/`runTerminalCommand`, not Claude Code's `Write`/
`Edit`/`Bash`). All three fixed; both this repo's own `.github/hooks/
pabel.json` and the separate `pabel-vscode-test` directory's copy were
regenerated at the corrected path. Full detail in `installers/vscode.py`
and `adapters/vscode.py`'s own docstrings.

### 15.3 One live miss prompted re-auditing every other "built to spec" adapter

Given a guess this specific and this wrong had gone unnoticed since the
original Phase 4 research, every other UNVERIFIED adapter's assumptions
were re-checked against current official docs the same way, rather than
continuing to trust research that had already been shown capable of being
confidently, plausibly wrong. Real, confirmed findings, most already
fixed (all in `connector/docs/coverage-matrix.md` in full, with sources):

- **Cursor**: response field names were camelCase
  (`agentMessage`/`userMessage`) - confirmed docs show snake_case
  (`agent_message`/`user_message`). Same class of mistake as vscode's
  path, just never caught because no live Cursor session had been tried
  either. `beforeMCPExecution`'s input field is confirmed `tool_input`,
  not `arguments` (the original priority order). Fixed.
- **Windsurf/Cascade**: reclassified DEGRADED, not just UNVERIFIED - the
  single biggest open question in the original research (does stderr ever
  reach the model, or only a human log) is now confirmed **negatively**:
  exit-code-2-plus-stderr only, no JSON response mechanism at all, stderr
  explicitly documented as human-visible-log-only. Transparent relay is
  confirmed impossible here, not merely unverified - a real vendor
  ceiling no amount of further live testing can lift. Per-hook input
  field names were also wrong (`command_line` not `command`;
  `mcp_server_name`/`mcp_tool_name`/`mcp_tool_arguments`, not `tool_name`/
  `arguments` - and `mcp_server_name` being a real, explicit field means
  `mcp_target` no longer needs Cursor's tool-name-heuristic fragility
  here). Fixed.
- **GitHub Copilot CLI**: config was written to a single `~/.copilot/
  hooks.json` file - confirmed docs show user-level hooks actually load
  from `*.json` files inside a `~/.copilot/hooks/` *directory*, and
  separately confirm a project-scoped `.github/hooks/*.json` alternative
  (same convention VS Code uses) that fits this package's own `--dir`
  install convention better. Switched to that. Fixed.
- **Gemini CLI**: held up essentially unchanged - config location, nested
  matcher/hooks shape, MCP naming convention, and the
  fold-content-into-`reason` design (confirmed `additionalContext`
  belongs to different, non-`BeforeTool` events) all matched current
  docs exactly. Included in the matrix for completeness, not because
  anything needed fixing - useful proof the re-audit wasn't just finding
  problems everywhere by construction.
- **OpenAI Codex CLI**: reclassified from DEGRADED to **NO ADAPTER**,
  moved to `docs/known-gaps.md` alongside Cline. The original research
  covered the Bash-only coverage gap but missed that Codex CLI's hooks
  feature is itself documented as "experimental (disabled by default,
  **not available on Windows**)" - confirmed independently via two
  separate searches. This is the identical blocking criterion already
  applied to Cline (employee machines can't be assumed non-Windows) - not
  a new policy, just a fact the first pass never surfaced. `adapters/
  codex_cli.py` deleted; `installers/codex_cli.py` rewritten as a no-op
  explainer matching `cline.py`'s pattern; removed from `registry.py`'s
  `ADAPTERS`.

Net effect: 79 connector tests (up from 73), all passing; three real,
previously-undetected schema/path bugs fixed (vscode, cursor,
copilot-cli); one adapter's achievable behavior corrected downward to
match a confirmed vendor ceiling (windsurf); one adapter removed entirely
on a newly-confirmed platform fact (codex-cli). Every one of these bugs
had been sitting in this package, unnoticed, since Phase 4 - none of them
were caught by the 73-test unit suite, because unit tests exercise this
package's own logic, never the vendor's actual file-loading and field
naming. The lesson this session keeps re-teaching itself (§2 point 2,
§12's zombie container, this section): confirming behavior against a real
external system beats re-deriving it from documentation or memory, and a
"BUILT-TO-SPEC" label is only as good as whichever spec was actually
read.
