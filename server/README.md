# PABEL service (Keycloak + PostgreSQL + MCP)

Lets an AI agent read `.abe`-encrypted documents through an MCP server,
restricted to whatever **both** the human operator behind it and the
agent product itself are entitled to see. A document section decrypts
only when the human's ABE attributes *and* the agent's ABE attributes
*together* satisfy its policy (e.g. `security_specialist and
agent_claude_code`) - an agent with no entry in the registry contributes
no attribute, so it implicitly fails any policy that requires one.

There is no "trust the agent" step, and no "trust a self-reported
identity" step either: every tool call re-verifies a Keycloak-issued
bearer token (MFA-capable browser login only - see `core.py`), and each
agent product runs as its own server instance (one `PABEL_AGENT_ID` per
process/container - see §8) whose contribution to a combined key is
further gated by a Keycloak realm role the current user's token must
carry (`agents_admin.py`, admin-registered only) - "block agent X for
user Y, not everyone" is just not assigning them that role. Runs on
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

## 4. Register an agent

```powershell
python agents_admin.py add claude-code "Claude Code" agent_claude_code agent_claude_code_user
```

Then, in the Keycloak admin console, create a realm role named
`agent_claude_code_user` and assign it to whichever users may use this
agent. An agent product never listed here simply never contributes an
attribute to any combined key - there is no separate deny-list to
maintain - and a user without the role gets the same implicit,
per-section denial as if the agent didn't exist.

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

Register it with your MCP client (see the project root's `.mcp.json`),
setting `PABEL_AGENT_ID` to the agent registered in step 4 (`PABEL_TRANSPORT`
defaults to `stdio`, i.e. this mode - no need to set it). Three read-only
tools are exposed:

- `whoami` - the authenticated user's and the calling agent's attributes,
  both re-verified on every call (never cached).
- `read_document(content, name="document")` - every section, each marked
  `accessible` and containing either its plaintext or `"[ACCESS DENIED]"`,
  decrypted with a key combining **both** principals' attributes.
  `content` is the `.abe` file's raw text, base64-encoded, wherever the
  agent found it (this server keeps no document store of its own to
  resolve a path against - and base64 avoids the JSON-shaped `.abe` text
  being misread as a structured argument in transit); `name` is only a
  label for the response and audit log.

## 8. Run the MCP server (remote, containerized)

For the "third-party device" deployment the project README describes:
one container per agent product (`Dockerfile` builds OpenABE from source
for Linux from `Claudiocli/openabe` - see `docs/phase2-engineering-notes.md`
for the handful of build-script issues found and worked around along the
way, none in the ported cryptography itself).

```powershell
nerdctl compose up -d mcp-server-claude-code
```

This runs `mcp_server.py` with `PABEL_TRANSPORT=streamable-http`, exposed
over HTTP instead of spawned per-session over stdio - `token_verifier.py`
wraps the same Keycloak verification `core.py` already does for stdio, via
the `mcp.server.auth` extension point the MCP SDK provides for exactly
this ("be a resource server in front of an external IdP" rather than
reimplementing OAuth). Add one more `mcp-server-<agent>` block to
`compose.yml` per additional registered agent (copy the existing block;
only `PABEL_AGENT_ID` and the published port need to change) - see
`docs/phase2-engineering-notes.md` §4 for why this is one container per
agent rather than one shared container differentiating agents some other
way.

## What this does and does not protect

Keycloak is the source of truth for the human's identity/attributes;
the agent registry (Postgres, admin-only) is the source of truth for
which agent products exist and what they contribute. Neither can be
changed by the user or by an agent through anything exposed here - only a
Keycloak admin (for users) or whoever runs `agents_admin.py` on this
server (for agents) can.

Access control is still fundamentally cryptographic: `read_document`
doesn't hide sections by policy, it just cannot decrypt the ones the
combined key doesn't satisfy. Whoever holds `authority/org.msk.cpabe` (the
master secret key) bypasses all of this, same as holding anyone's raw ABE
key would - this server is the only thing that ever touches it, and it is
never written to `.mcp.json`, an agent's environment, or anywhere an agent
could read it.

The `.abe` on-disk format (`document.py`) is this project's current
working format, not a settled spec - it may change.
