# PABEL - (**P**lugin **ABE** for **L**LM)

Lets an AI coding agent read `.abe` (CP-ABE-encrypted) documents on a
human's behalf, decrypting only the sections **both** the human's *and*
the calling agent product's attributes together satisfy. The agent never
sees raw ciphertext or performs decryption itself - direct access to an
`.abe` file is blocked and transparently relayed to a dedicated MCP server
instead, which does the actual decryption and re-verifies identity on
every call.

For the original use case (UC1) and the reasoning behind the architecture,
see [`docs/README.md`](docs/README.md). For the full build history -
every design decision, bug found, and its fix - see
[`docs/phase2-engineering-notes.md`](docs/phase2-engineering-notes.md).

## Repository layout

| Path | What it is |
|---|---|
| `server/` | The MCP server itself: CP-ABE via OpenABE, Keycloak auth (MFA-capable browser login only), PostgreSQL agent/key registry, audit log. |
| `claude-plugin/` | The Claude Code plugin - **verified working end-to-end**, ready to install. |
| `connector/` | `pabel-connector` - the agent-agnostic core (Strategy pattern) plus adapters extending the same enforcement to other AI coding agents (Cursor, Windsurf, VS Code, GitHub Copilot CLI, Gemini CLI, a partial fit for OpenAI Codex CLI). |
| `documents/` | `Test.abe`, a demo encrypted fixture used throughout testing. |
| `docs/` | The original use-case writeup and the full engineering log. |

## Quickstart

### 1. Deploy the server (once, by whoever runs the company's PABEL deployment)

```powershell
cd server
cp .env.example .env        # fill in real random values - see the comments in the file
python generate_realm.py
nerdctl compose up -d       # Keycloak + PostgreSQL
python setup_user_profile.py
python -c "import db; db.init_schema()"
python -c "import abe; abe.setup_authority()"
python agents_admin.py add claude-code "Claude Code" agent_claude_code agent_claude_code_user
python agents_admin.py create-installation claude-code --label "alice's laptop"
nerdctl compose up -d mcp-server   # one single, shared server for every agent
```

`create-installation` prints a `client_id`/`client_secret` pair once, for
each employee - hand it to them out of band, for use in step 2. Full
detail, including what each step actually does and why:
[`server/README.md`](server/README.md).

### 2. Install the enforcement for your AI coding agent (each employee, on their own machine)

**Claude Code** (the only agent verified against a real, live install):

```
/plugin marketplace add <path-or-git-url-to-claude-plugin>
/plugin install pabel@pabel-marketplace
python <plugin-install-path>/enroll.py CLIENT_ID CLIENT_SECRET   # from step 1
```

Full configuration: [`claude-plugin/pabel/README.md`](claude-plugin/pabel/README.md).

**Any other supported agent** (Cursor, Windsurf, VS Code, GitHub Copilot
CLI, Gemini CLI - and a deliberately partial, Bash-only fit for OpenAI
Codex CLI):

```
pip install -e connector
pabel-connector install <agent> --dir . --client-id CLIENT_ID --client-secret CLIENT_SECRET
```

`--client-id`/`--client-secret` are the credential `create-installation`
printed in step 1 - proof of *which installation* this is, verified by
the server on every call (never just trusted because of which URL it
reached). `pabel-connector list` shows every registered agent and its
verification status - **read this before trusting anything but Claude
Code in production**, since most adapters are built to each vendor's own
documentation and not yet confirmed against a live install. Full detail:
[`connector/README.md`](connector/README.md) and
[`connector/docs/coverage-matrix.md`](connector/docs/coverage-matrix.md).

### 3. Log in as yourself

```
pabel-connector login   # or claude-plugin/pabel/login.py for the Claude Code plugin specifically
```

Opens the system browser at Keycloak's own login page (MFA included,
whatever the realm requires) - this is the *human* identity check every
decryption ultimately depends on, separate from the installation
credential enrolled in step 2 (both are required together - see
`server/core.py`'s `resolve_agent()`).
