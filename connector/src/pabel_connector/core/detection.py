"""Detection logic: does a tool call touch an .abe file or the documents/
folder, and if so, can exactly one concrete file be identified for relay?

The regex set and the spaced-path/QUOTED_SEGMENT handling below were
originally ported from the standalone Claude Code plugin's own hook script
(since removed - see docs/phase2-engineering-notes.md sec 9 for the bugs
already found and fixed there) - nothing here was ever Claude-Code-
specific, since it only ever operates on the serialized tool_input, never
on a Claude-only field name, so the port carried over unchanged.
"""

import json
import re
from pathlib import Path

from ..pabel_client import agent_session, session

ENCRYPTED_FILE = re.compile(r"(?i)[^\s\"'`]*\.abe\b")
DOCUMENTS_PATH = re.compile(r"(?i)(^|[\\/])documents([\\/]|$)")
OABE_BINARY = re.compile(r"(?i)\boabe_(setup|keygen|enc|dec)\b")
QUOTED_SEGMENT = re.compile(r'"([^"]*)"|\'([^\']*)\'')

# The PABEL MCP server's own tool calls are always sanctioned - detection
# exists to catch attempts to touch .abe files *outside* that path, not to
# fight it. Without this allowlist, a direct, legitimate
# mcp_target=("pabel", "read_document") call whose `name` argument happens
# to end in ".abe" would match ENCRYPTED_FILE and could be wrongly denied
# as ambiguous - decide() checks this before calling mentions_target at all.
PABEL_MCP_SERVER_NAME = "pabel"

# mcp_local_server.py - this package's own bundled local MCP server,
# registered per-agent by installers/base.py's mcp_server_command() - is a
# second, distinct always-sanctioned target. Its whoami/read_document/login
# tools resolve this installation's own identity internally (baked in at
# registration time), so unlike PABEL_MCP_SERVER_NAME above, no agent_token
# needs injecting - see decide.py's two separate mcp_target branches.
PABEL_CONNECTOR_MCP_SERVER_NAME = "pabel-connector"


def mentions_target(tool_input):
    text = json.dumps(tool_input)
    return bool(ENCRYPTED_FILE.search(text) or DOCUMENTS_PATH.search(text))


def touches_pabel_credential_store(tool_input):
    """True if this call's input mentions the on-disk path of this
    installation's own PABEL secrets - the human session (session.py) or
    an agent installation's client_secret/access_token (agent_session.py).
    Nothing but this package's own code should ever read these directly: a
    model doing so would leak a live, usable credential straight into its
    own context, bypassing every other check in this file entirely (they
    are never .abe files or under documents/, so mentions_target() alone
    would wave this through). Checked against the same DATA_DIR
    session.py/agent_session.py themselves resolve (respects
    PABEL_PLUGIN_DATA_DIR), not a guessed path.

    Walks tool_input's actual string values rather than json.dumps()-ing it
    like mentions_target() does: JSON-encoding doubles backslashes, which
    would never match a raw Windows path taken straight from
    session.SESSION_FILE/agent_session.CREDENTIALS_FILE."""
    targets = [str(session.SESSION_FILE), str(agent_session.CREDENTIALS_FILE)]

    def walk(value):
        if isinstance(value, str):
            return any(target in value for target in targets)
        if isinstance(value, dict):
            return any(walk(v) for v in value.values())
        if isinstance(value, list):
            return any(walk(v) for v in value)
        return False

    return walk(tool_input)


def invokes_oabe_binary(tool_input):
    return bool(OABE_BINARY.search(json.dumps(tool_input)))


# Every hook-based installer's own hook/MCP registration file (installers/
# claude_code.py, vscode.py, copilot_cli.py, cursor.py, windsurf.py -
# CONFIG_RELATIVE_PATH/MCP_CONFIG_RELATIVE_PATH/GLOBAL_CONFIG_RELATIVE_PATH),
# as project-relative fragments. codex_family.py (Codex CLI/ChatGPT desktop)
# is deliberately absent - neither has a hook for decide() to protect in the
# first place, same reason they're absent from installers/registry.py's
# ADAPTERS. A --global install's path is still caught here without knowing
# base_dir: e.g. Claude Code's global `~/.claude/settings.json` still
# contains the substring ".claude/settings.json", so listing the relative
# fragment once covers both scopes.
_HOOK_CONFIG_RELATIVE_PATHS = [
    ".claude/settings.json",
    ".mcp.json",
    ".github/hooks/pabel.json",
    ".vscode/mcp.json",
    ".github/hooks/pabel-copilot-cli.json",
    ".copilot/hooks/pabel-copilot-cli.json",
    ".cursor/hooks.json",
    ".windsurf/hooks.json",
    ".codeium/windsurf/hooks.json",
]
_HOOK_CONFIG_CANDIDATES = [
    variant
    for rel in _HOOK_CONFIG_RELATIVE_PATHS
    for variant in (rel, rel.replace("/", "\\"))
]


def is_pabel_hook_config_target(path):
    """True if `path` (a write's actual target - see DENY_MUTATING's
    write_target-only check in decide.py, same reasoning: a write whose
    *content* merely discusses one of these files must never be confused
    with a write *to* one) names one of this package's own hook/MCP
    registration files.

    Nothing legitimate ever needs to rewrite these except pabel-connector's
    own install()/uninstall() - which never run as a tool call decide() would
    see in the first place (they're invoked directly by a human via the
    `pabel-connector` CLI, outside any agent's hook). A model rewriting one
    itself is exactly the bypass this exists to catch: the agent_id argument
    baked into that file at install time is the only thing that ties "this
    hook fired" to "use this installation's credential" (see decide()'s own
    docstring) - if a model could edit it freely, it could redirect its own
    hook to authenticate as any other agent product installed on the same
    machine, or repoint the deployed server's own URL entirely."""
    if not path:
        return False
    return any(candidate in path for candidate in _HOOK_CONFIG_CANDIDATES)


def _is_pabel_connector_source_checkout():
    """True only when the current project IS this connector's own source
    repo (the PABEL monorepo), detected structurally by looking for its
    own known layout relative to cwd - deliberately NOT by asking
    importlib/pip where the installed pabel_connector package resolves to.
    That signal is useless here: `pip install -e connector` (this
    project's own documented, current install method) makes an editable
    install's __file__ point at this same source path even from a
    completely unrelated downstream project that merely depends on it -
    the only way to tell "this repo" apart from "a project that depends on
    it" is to ask about the *project*, never the *package*."""
    return (Path.cwd() / "connector" / "src" / "pabel_connector"
            / "core" / "decide.py").exists()


_PABEL_CONNECTOR_INTERNALS = re.compile(
    r"(?i)\bpabel_connector\b|\bpabel-connector-hook\b")
"""Two distinct spellings for the same thing, both denied: the Python
package name (`pabel_connector`, underscore - `python -m
pabel_connector.hook <key>`, `from pabel_connector import ...`) and the
installed console-script name for the hook entry point specifically
(`pabel-connector-hook`, hyphens - `pyproject.toml`'s `[project.scripts]`).
A single `\bpabel_connector\b` pattern does NOT catch the second form: a
hyphen is a non-word character, so "pabel-connector-hook" never contains
the substring "pabel_connector" at all - confirmed live 2026-08 while
explaining why hardcoding an adapter's own agent_id is safe once
DENY_CONFIG_TAMPER exists, which prompted checking whether *this* check
had the equivalent gap for the other bypass class (running the hook binary
directly with a hand-picked key/agent_id, rather than rewriting the config
file that would normally choose it). Deliberately does NOT also match the
plain `pabel-connector` CLI (install/uninstall/login/doctor) - that command
needs a real, already-admin-issued client_secret to matter and doesn't let
a caller pick decide()'s agent_id at call time the way `-hook` does; folding
it in here would just block routine, legitimate CLI usage a model might
reasonably run on a human's behalf."""


def invokes_pabel_connector_internals(tool_input):
    """True if an execute-type call's command tries to import/invoke this
    package's own modules, or run its `pabel-connector-hook` entry point
    directly, with a hand-picked key/agent_id - never something a model has
    a legitimate reason to do in a normal project consuming pabel-connector
    as a dependency: the hook is the only sanctioned way any of this ever
    runs, and the *real* hook's own invocation never appears here as a
    tool_input being evaluated at all (it IS the thing evaluating - see
    hook.py, and decide.py's own docstring). Both this and DENY_CONFIG_TAMPER
    exist to protect the same invariant (which agent_id a given hook
    invocation uses) from two different angles: DENY_CONFIG_TAMPER stops the
    config file from being rewritten to name a different agent_id; this
    stops the hook binary from being invoked directly with one instead, bypassing
    the config file - and by extension the real per-agent hook - entirely.

    The `pabel_connector` (module) form is exactly the bypass a live vscode
    Copilot session was found to use (docs/phase2-engineering-notes.md, the
    GitHub Copilot.md transcript): with no hook wired yet, it called
    `relay.read_document(..., 'claude-code')` directly from a Bash
    one-liner, borrowing a different installation's credential entirely
    outside anything this file could see.

    Skipped entirely inside this package's own source checkout
    (`_is_pabel_connector_source_checkout()`), where invoking these
    modules/binaries directly - tests, agents_admin.py, doctor, manual live
    verification - is routine, legitimate development work, not a bypass."""
    if _is_pabel_connector_source_checkout():
        return False
    text = json.dumps(tool_input)
    return bool(_PABEL_CONNECTOR_INTERNALS.search(text))


def find_relayable_file(tool_input):
    """The first value in tool_input that names an existing, single .abe
    file on disk - or None if nothing that concrete can be identified (a
    directory, a glob pattern, several candidates, a shell pipeline).
    Deliberately conservative: no match here means "deny, don't guess"."""
    candidates = []

    def consider(candidate):
        path = Path(candidate)
        if path.is_file():
            candidates.append(path)

    def walk(value):
        if isinstance(value, str):
            # A structured field (Read/Edit/Grep's file_path/path) is the
            # whole path itself - checked first since a regex substring
            # search would break on any space in the path.
            if ENCRYPTED_FILE.search(value):
                consider(value)
            # Free text (a Bash command embedding a path among other
            # tokens): a quoted span may itself contain spaces (a quoted
            # Windows path), so check whole quoted segments first...
            for m in QUOTED_SEGMENT.finditer(value):
                segment = m.group(1) if m.group(1) is not None else m.group(2)
                if ENCRYPTED_FILE.search(segment):
                    consider(segment)
            # ...then fall back to a bare substring match for an
            # unquoted, space-free path.
            for m in ENCRYPTED_FILE.finditer(value):
                consider(m.group(0).strip("\"'`"))
        elif isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    walk(tool_input)
    return candidates[0] if candidates else None
