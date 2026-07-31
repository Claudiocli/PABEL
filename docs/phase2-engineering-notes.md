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
