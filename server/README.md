# PABEL service (Keycloak + PostgreSQL + MCP)

Lets an AI agent read `.abe`-encrypted documents through an MCP server,
restricted to whatever **both** the human operator behind it and the
agent product itself are entitled to see. A document section decrypts
only when the human's ABE attributes *and* the agent's ABE attributes
*together* satisfy its policy (e.g. `security_specialist and
agent_claude_code`) - an agent with no entry in the registry contributes
no attribute, so it implicitly fails any policy that requires one.

There is no "trust the agent" step, and no "trust a self-reported
identity" step either: every tool call re-verifies **two** independent
Keycloak-issued bearer tokens - the human's (MFA-capable browser login
only - see `core.py`) and the calling agent installation's own (a
`client_credentials` token, admin-provisioned per installation - see §4)
- and the agent's contribution to a combined key is further gated by a
Keycloak realm role the current user's token must carry (`agents_admin.py`,
admin-registered only) - "block agent X for user Y, not everyone" is just
not assigning them that role. A single shared server instance serves
every agent product and every installation of it (see §8) - "which agent
is calling" is proven cryptographically on every request, never inferred
from which container or URL was reached. Runs on
**Rancher Desktop** (containerd/`nerdctl` engine); Moby (`dockerd`), if
that engine is selected instead, also works since it reads the same open
`compose.yml`.

## 1. Backing services (Keycloak + PostgreSQL)

```powershell
cd server
cp .env.example .env
# edit .env: set KC_BOOTSTRAP_ADMIN_PASSWORD, POSTGRES_PASSWORD (and
# PABEL_DB_DSN's password to match), ALICE/BOB/CHARLIE_PASSWORD
# to real random values, e.g.:
python -c "import secrets; print(secrets.token_urlsafe(24))"

python generate_realm.py     # writes realm-org.json (gitignored) from the template
nerdctl compose up -d        # starts Keycloak (realm "pabel") + Postgres
python setup_user_profile.py  # once Keycloak is up: declares abe_attributes
                              # in the User Profile (admin-edit only) - see
                              # its docstring for why this can't be part of
                              # the realm-import JSON itself
```

Keycloak console at http://localhost:8080 (admin / the password you set).
The `pabel` realm ships three demo users - alice (`livello=3`, `ruolo_ceo`,
`dev`, `security_specialist`), bob (`livello=2`, `ruolo_hr`), charlie
(`livello=1`, `ruolo_dev`) - each with the passwords from `.env`, and a
public client `pabel` (Authorization Code + PKCE only - no direct
password grant, so realm MFA can never be skipped). **Only a realm admin
can add or change a user's `abe_attributes`** (User Profile permissions:
`view: admin,user`, `edit: admin`, set by `setup_user_profile.py`) -
neither the user nor any agent can self-modify them.

## 2. Database schema

```powershell
python -c "import db; db.init_schema()"
```

Creates `agents`, `user_keys`, `agent_keys`, `audit_log` (see
`schema.sql`) if they don't already exist.

## 3. ABE authority

```powershell
python -c "import abe; abe.setup_authority()"
```

Creates `authority/org.mpk.cpabe` (public parameters) and
`authority/org.msk.cpabe` (**master secret key** - this server's, never
distributed elsewhere; gitignored). One-time per deployment: regenerating
it invalidates every ciphertext already encrypted under the previous one.

## 4. Register an agent product and create an installation

```powershell
python agents_admin.py add claude-code "Claude Code" agent_claude_code agent_claude_code_user
```

Then, in the Keycloak admin console, create a realm role named
`agent_claude_code_user` and assign it to whichever users may use this
agent. An agent product never listed here simply never contributes an
attribute to any combined key - there is no separate deny-list to
maintain - and a user without the role gets the same implicit,
per-section denial as if the agent didn't exist.

Registering the *product* isn't enough on its own: every request also
carries its own **per-installation** credential - a Keycloak
`client_credentials` client, cryptographically verified fresh on every
call (`core.resolve_agent()`), never inferred from which server/URL was
reached (see `docs/phase2-engineering-notes.md` for why this replaced an
earlier one-container-per-agent design). Create one installation per
employee:

```powershell
python agents_admin.py create-installation claude-code --label "alice's laptop"
```

Prints a `client_id`/`client_secret` pair **once** - this is exclusively an
admin action, never reachable over the network from an employee's own
machine. Hand both values to that employee out of band (the same channel
used for their own Keycloak credentials), for them to run
`pabel-connector install claude-code --client-id ... --client-secret ...`
(or `enroll.py` for the Claude Code plugin specifically - see
`claude-plugin/pabel/README.md`). Revoke one specific installation without
touching any other with `agents_admin.py revoke-installation CLIENT_ID`;
`list-installations [AGENT_ID]` shows what's currently registered.

## 5. Python environment

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

## 6. Log in as a human

```powershell
.venv\Scripts\python login.py
```

Opens the system browser at Keycloak's own hosted login page -
including whatever MFA the realm requires (the demo users all have
`CONFIGURE_TOTP` set). Writes tokens (never a password) to
`.session.json`, which `mcp_server.py` re-verifies on every single tool
call - this is the only way to establish a human identity here; there is
no password-grant fallback to fall back to.

## 7. Run the MCP server (local, stdio)

Register it with your MCP client (see the project root's `.mcp.json`) -
`PABEL_TRANSPORT` defaults to `stdio`, i.e. this mode, no env var needed
for it. Two read-only tools are exposed, both requiring an `agent_token`
argument (this installation's own Keycloak `client_credentials` access
token - see step 4 and `core.resolve_agent()`). The relay hook obtains and
injects this automatically on every call it makes; a *direct* model call
to either tool needs it supplied too, which `pabel-connector`'s Claude
Code adapter also does transparently (see `connector/README.md`) so the
model itself never needs to see or hold the credential:

- `whoami(agent_token)` - the authenticated user's and the calling agent
  installation's attributes, both re-verified on every call (never
  cached).
- `read_document(content, agent_token, name="document")` - every section,
  each marked `accessible` and containing either its plaintext or
  `"[ACCESS DENIED]"`, decrypted with a key combining **both** principals'
  attributes. `content` is the `.abe` file's raw text, base64-encoded,
  wherever the agent found it (this server keeps no document store of its
  own to resolve a path against - and base64 avoids the JSON-shaped `.abe`
  text being misread as a structured argument in transit); `name` is only
  a label for the response and audit log.

## 8. Run the MCP server (remote, containerized)

For the "third-party device" deployment the project README describes: a
single shared container serves every agent product and every installation
of it (`Dockerfile` builds OpenABE from source for Linux from
`Claudiocli/openabe` - see `docs/phase2-engineering-notes.md` for the
handful of build-script issues found and worked around along the way,
none in the ported cryptography itself).

```powershell
nerdctl compose up -d mcp-server
```

This runs `mcp_server.py` with `PABEL_TRANSPORT=streamable-http`, exposed
over HTTP instead of spawned per-session over stdio - `token_verifier.py`
wraps the same Keycloak verification `core.py` already does for stdio, via
the `mcp.server.auth` extension point the MCP SDK provides for exactly
this ("be a resource server in front of an external IdP" rather than
reimplementing OAuth). One deployment serves every agent product and
installation - see `docs/phase2-engineering-notes.md` for why this
replaced an earlier one-container-per-agent design, and step 4 above for
how a new agent product or installation is added without touching this
service definition at all.

## What this does and does not protect

Keycloak is the source of truth for the human's identity/attributes; the
agent product registry and the per-installation registry (both Postgres,
admin-only) are the source of truth for which agent products exist, what
they contribute, and which real installations may act as them. None of
this can be changed by the user or by an agent through anything exposed
here - only a Keycloak admin (for users) or whoever runs `agents_admin.py`
on this server (for agent products and installations) can.

Access control is still fundamentally cryptographic: `read_document`
doesn't hide sections by policy, it just cannot decrypt the ones the
combined key doesn't satisfy. Whoever holds `authority/org.msk.cpabe` (the
master secret key) bypasses all of this, same as holding anyone's raw ABE
key would - this server is the only thing that ever touches it, and it is
never written to `.mcp.json`, an agent's environment, or anywhere an agent
could read it.

The `.abe` on-disk format (`document.py`) is this project's current
working format, not a settled spec - it may change.
