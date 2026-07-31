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

Repeat the `add`/`create-installation` pair once per agent **product**
you want to support (`cursor`, `vscode`, ...) and once per **employee**
installing it, respectively - the server itself (`compose up`) is started
only once regardless of how many agents/employees end up using it.
`create-installation` prints a `client_id`/`client_secret` pair once, for
that one employee - hand it to them out of band, for use in step 2. Full
detail, including what each step actually does and why:
[`server/README.md`](server/README.md).

### 2. Install the enforcement for your AI coding agent (each employee, on their own machine)

One command, the same for **any** supported agent:

```
pip install -e connector
pabel-connector install <agent> --dir . --client-id CLIENT_ID --client-secret CLIENT_SECRET
```

`<agent>` is `claude-code`, `cursor`, `windsurf`, `vscode`, `copilot-cli`,
`gemini-cli`, or `codex-cli` (`pabel-connector list` shows the full set
and each one's verification status). `--client-id`/`--client-secret` are
the credential `create-installation` printed in step 1 for this specific
employee - proof of *which installation* this is, verified by the server
on every call, never just trusted because of which URL it reached.

**Read the verification status before trusting anything in production**:
today only `claude-code` is confirmed against a real, live install - every
other adapter is built strictly to each vendor's own documentation and not
yet tried live. Full detail: [`connector/README.md`](connector/README.md)
and [`connector/docs/coverage-matrix.md`](connector/docs/coverage-matrix.md).

**Claude Code specifically** also needs its dedicated marketplace plugin -
the command above already stores the installation credential and prints
these same two lines, since Claude Code's own plugin mechanism replaces
hand-written hook config entirely (nothing for `pabel-connector` to write):

```
/plugin marketplace add <path-or-git-url-to-claude-plugin>
/plugin install pabel@pabel-marketplace
```

Full configuration: [`claude-plugin/pabel/README.md`](claude-plugin/pabel/README.md)
(also has `enroll.py`/`login.py`, self-contained equivalents of `install`/
`login` above for anyone who only wants the Claude Code plugin and would
rather not install `connector/` at all).

### 3. Log in as yourself

```
pabel-connector login   # or claude-plugin/pabel/login.py for the Claude Code plugin specifically
```

Opens the system browser at Keycloak's own login page (MFA included,
whatever the realm requires) - this is the *human* identity check every
decryption ultimately depends on, separate from the installation
credential enrolled in step 2 (both are required together - see
`server/core.py`'s `resolve_agent()`).
