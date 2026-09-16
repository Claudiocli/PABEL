# PABEL service (Keycloak + PostgreSQL + MCP)

Lets an AI agent read `.abe`-encrypted documents through an MCP server,
restricted to what **both** the human behind it and the agent product itself
are entitled to see. A section decrypts only when the human's ABE attributes
*and* the agent's together satisfy its policy (e.g. `security_specialist and
agent_claude_code`).

There is no "trust the agent" step. Every tool call re-verifies two
independent Keycloak tokens - the human's (browser login, MFA-capable) and
the calling installation's (`client_credentials`, admin-provisioned) - and
the agent's contribution is further gated by a realm role the user's token
must carry. Blocking agent X for user Y is just not assigning that role.

One shared server serves every agent product and every installation of it.
Which agent is calling is proven cryptographically per request, never
inferred from which container or URL was reached.

Runs on **Rancher Desktop** (containerd/`nerdctl`); Moby works too, same
`compose.yml`.

## Setup

**1. Backing services**

```powershell
cd server
cp .env.example .env
# Set KC_BOOTSTRAP_ADMIN_PASSWORD, POSTGRES_PASSWORD (matching PABEL_DB_DSN),
# and ALICE/BOB/CHARLIE_PASSWORD to real random values:
python -c "import secrets; print(secrets.token_urlsafe(24))"

python generate_realm.py        # writes realm-org.json (gitignored)
nerdctl compose up -d           # Keycloak (realm "pabel") + Postgres
python setup_user_profile.py    # declares abe_attributes, admin-edit only
```

Console at http://localhost:8080. The realm ships three demo users - alice
(`livello=3`, `ruolo_ceo`, `dev`, `security_specialist`), bob (`livello=2`,
`ruolo_hr`), charlie (`livello=1`, `ruolo_dev`) - and a public client `pabel`
using Authorization Code + PKCE only, so realm MFA can never be skipped.
**Only a realm admin can change a user's `abe_attributes`.**

**2. Schema and ABE authority**

```powershell
python -c "import db; db.init_schema()"       # agents, agent_installations,
                                              # agent_keys, audit_log

# authority/org.{mpk,msk}.cpabe - INSIDE the container, not on the host:
nerdctl compose run --rm mcp-server python -c "import abe; abe.setup_authority()"
```

The master secret key is one-time per deployment: regenerating it
invalidates every existing ciphertext.

**Generate it with the same OpenABE build that will consume it.** The server
is the only component that runs `oabe_keygen`/`oabe_dec`, and it runs inside
the container against the Linux build compiled by the Dockerfile. An
authority produced by a *different* build - the Windows `oabe_*.exe` on the
host, or simply an older checkout of the fork - serialises a few bytes
differently, and the container's build then rejects it with `caught
exception: Invalid function input` while `authority_exists()` still reports
True, because that only checks the public `mpk`. Encrypting documents has to
use a matching build for the same reason (see docs/known-gaps.md).

**3. Python environment**

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

**4. Register an agent product**

```powershell
python agents_admin.py add claude-code "Claude Code" agent_claude_code agent_claude_code_user
```

Then create the realm role (`agent_claude_code_user`) in the Keycloak console
and assign it to whoever may use that agent. An unregistered product
contributes no attribute and so implicitly fails any policy requiring one -
there is no deny-list to maintain.

**5. Create one installation per employee**

```powershell
python agents_admin.py create-installation claude-code --label "alice's laptop"
```

Prints a `client_id`/`client_secret` **once**. Hand both to the employee out
of band; they run `pabel-connector install <agent> --client-id ...
--client-secret ...`. Revoke one without touching the others via
`revoke-installation CLIENT_ID`; `list-installations [AGENT_ID]` shows what
exists.

**6. Log in as a human**

```powershell
.venv\Scripts\python login.py
```

Opens the browser at Keycloak's hosted login, MFA included. Writes tokens
(never a password) to `.session.json`, re-verified on every tool call. There
is no password-grant fallback.

## Running the MCP server

**Local (stdio)** - the default, no env var needed. Register it with your MCP
client (see the project root's `.mcp.json`).

**Remote (containerized)**:

```powershell
nerdctl compose up -d mcp-server
```

Runs with `PABEL_TRANSPORT=streamable-http`; `token_verifier.py` wraps the
same Keycloak verification `core.py` does for stdio.

Both expose the same read-only tools, all requiring an `agent_token` (this
installation's `client_credentials` token - the relay hook injects it
automatically, so the model never holds it):

- `whoami(agent_token)` - the user's and the installation's attributes,
  re-verified per call, never cached.
- `read_document(content, agent_token, name="document")` - every section,
  marked `accessible`, containing either its plaintext or `"[ACCESS DENIED]"`.
  `content` is the `.abe` file's raw text, base64-encoded (this server keeps
  no document store to resolve a path against).
- `report_denial(agent_token, decision_kind, reason, tool_name)` - records a
  client-side denial in the same audit trail.

**Where to look when checking what happened** (run from `server/`):

- Accountability trail: `state/audit.jsonl` on the host. A local
  non-containerized run writes a separate `audit.jsonl` next to itself - the
  two are different processes, don't expect one to contain the other.
- Process output: `nerdctl compose logs -f mcp-server`.

## Audit-log keypair (optional)

```powershell
python agents_admin.py generate-audit-keypair --out-dir <dir>
python agents_admin.py decrypt-audit-log <log-file> --private-key <path>
```

One company-wide keypair for the connector's local, **confidentiality-only**
log. The private key decrypts every employee's log - store it accordingly. It
is explicitly not an integrity guarantee; see `connector/docs/known-gaps.md`.

## What this does and does not protect

Keycloak is the source of truth for the human; the Postgres registries
(admin-only) for which agent products exist and which installations may act
as them. Neither a user nor an agent can change any of it through anything
exposed here.

Access control is cryptographic, not filtered: `read_document` doesn't hide
sections by policy, it simply cannot decrypt the ones the combined key
doesn't satisfy. Whoever holds `authority/org.msk.cpabe` bypasses everything
- this server is the only thing that touches it.

Once a decrypted result is legitimately handed back and lands on the
caller's disk (e.g. `connector/pabel_client/materialize.py`), this server
cannot reach back and revoke or refresh it. It is deliberately stateless
about documents - building that would mean keeping a document store plus a
way to reach a device unprompted, both explicitly avoided. The mitigation is
bounding a local copy's lifetime client-side.

This server only ever **decrypts**. Producing `.abe` documents is the
deploying company's own responsibility - see
`connector/docs/known-gaps.md`.

The `.abe` on-disk format (`document.py`) is this project's current working
format, not a settled spec.
