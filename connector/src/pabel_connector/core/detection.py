"""Detection logic: does a tool call touch an .abe file or the documents/
folder, and if so, can exactly one concrete file be identified for relay?

Agent-agnostic throughout - everything here operates on the serialized
tool_input, never on any one vendor's field names.
"""

import json
import os
import re
from pathlib import Path

from ..pabel_client import agent_session, session

ENCRYPTED_FILE = re.compile(r"(?i)[^\s\"'`]*\.abe\b")
DOCUMENTS_PATH = re.compile(r"(?i)(^|[\\/])documents([\\/]|$)")
OABE_BINARY = re.compile(r"(?i)\boabe_(setup|keygen|enc|dec)\b")
QUOTED_SEGMENT = re.compile(r'"([^"]*)"|\'([^\']*)\'')

# Always-sanctioned targets, checked by decide() before mentions_target() -
# otherwise a legitimate read_document call whose `name` ends in ".abe" would
# match ENCRYPTED_FILE and be wrongly denied as ambiguous.
PABEL_MCP_SERVER_NAME = "pabel"

# This package's own bundled local MCP server. Unlike the deployed one above,
# it resolves this installation's identity internally, so decide() needs no
# agent_token injection for it - hence two separate mcp_target branches.
PABEL_CONNECTOR_MCP_SERVER_NAME = "pabel-connector"


def mentions_target(tool_input):
    text = json.dumps(tool_input)
    return bool(ENCRYPTED_FILE.search(text) or DOCUMENTS_PATH.search(text))


def touches_pabel_credential_store(tool_input):
    """True if this call's input mentions this installation's own secrets -
    the human session or an agent's client_secret/access_token. A model
    reading either would leak a live credential into its own context, and no
    other check here would catch it (they're never .abe files).

    Walks the input's string values rather than json.dumps()-ing it:
    JSON-encoding doubles backslashes, which would never match a raw Windows
    path.

    Both sides go through os.path.normcase(), which is a bypass fix rather
    than tidying: on Windows `C:/Users/.../agent_credentials.json` opens the
    same file as the backslash form, and the filesystem is case-insensitive,
    so a plain substring match let either spelling walk straight past.
    normcase is platform-correct for free - a no-op on POSIX, so it can't
    start wrongly denying distinct files on a case-sensitive filesystem."""
    targets = [os.path.normcase(str(session.SESSION_FILE)),
               os.path.normcase(str(agent_session.CREDENTIALS_FILE))]

    def walk(value):
        if isinstance(value, str):
            normalized = os.path.normcase(value)
            return any(target in normalized for target in targets)
        if isinstance(value, dict):
            return any(walk(v) for v in value.values())
        if isinstance(value, list):
            return any(walk(v) for v in value)
        return False

    return walk(tool_input)


def invokes_oabe_binary(tool_input):
    return bool(OABE_BINARY.search(json.dumps(tool_input)))


# Every hook-based installer's own config file, as relative fragments - which
# also catches a --global install without knowing base_dir, since an absolute
# path still contains the fragment. codex_family.py is deliberately absent:
# neither product has a hook for decide() to protect.
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
    # opencode's enforcement IS this .js file, so rewriting it replaces the
    # whole path in place with no credential ever being read. No leading dot,
    # so one entry covers both project and global scope; the singular
    # "plugin/" is opencode's backwards-compatible spelling, also accepted.
    "opencode/plugins/pabel.js",
    "opencode/plugin/pabel.js",
    "opencode.json",
]
_HOOK_CONFIG_CANDIDATES = [
    os.path.normcase(variant)
    for rel in _HOOK_CONFIG_RELATIVE_PATHS
    for variant in (rel, rel.replace("/", "\\"))
]


def is_pabel_hook_config_target(path):
    """True if `path` names one of this package's own hook/MCP config files.

    Takes a write's actual target, never its content: a write that merely
    *discusses* one of these files must not be confused with a write to one.

    Only `pabel-connector install`/`uninstall` legitimately rewrite these, and
    neither runs as a tool call decide() would ever see. A model rewriting one
    is the bypass this catches: the agent_id baked in at install time is what
    ties "this hook fired" to "use this credential", so a free edit could
    authenticate as another installed product, or repoint the server URL.

    normcase for the same reason touches_pabel_credential_store() uses it.
    """
    if not path:
        return False
    return any(candidate in os.path.normcase(path)
               for candidate in _HOOK_CONFIG_CANDIDATES)


def _is_pabel_connector_source_checkout():
    """True only when the current project IS this connector's own source repo.

    Detected from cwd's layout, deliberately not from where importlib resolves
    pabel_connector: an editable install's __file__ points at this same source
    path even from an unrelated project that merely depends on it, so only the
    *project* can tell the two apart."""
    return (Path.cwd() / "connector" / "src" / "pabel_connector"
            / "core" / "decide.py").exists()


_PABEL_CONNECTOR_INTERNALS = re.compile(
    r"(?i)\bpabel_connector\b|\bpabel-connector-hook\b")
"""Both spellings are needed: the Python package name (underscore) and the
hook's console-script name (hyphens). One `\bpabel_connector\b` pattern does
NOT catch the second - a hyphen is a non-word character, so
"pabel-connector-hook" never contains "pabel_connector" at all.

Deliberately does not match the plain `pabel-connector` CLI: it needs an
admin-issued secret to matter and can't pick decide()'s agent_id at call time
the way `-hook` can, so denying it would only block legitimate usage."""


def invokes_pabel_connector_internals(tool_input):
    """True if an execute-type call tries to import this package's modules or
    run its hook entry point directly with a hand-picked agent_id.

    This and DENY_CONFIG_TAMPER protect the same invariant - which agent_id a
    hook invocation uses - from two angles: that one stops the config file
    being rewritten to name a different agent_id, this stops the hook being
    invoked directly with one, bypassing the config entirely. A live Copilot
    session was found doing exactly this, calling `relay.read_document(...,
    'claude-code')` from a Bash one-liner to borrow another installation's
    credential.

    Skipped inside this package's own source checkout, where invoking these
    modules directly is routine development work."""
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
