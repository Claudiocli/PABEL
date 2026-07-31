"""Detection logic: does a tool call touch an .abe file or the documents/
folder, and if so, can exactly one concrete file be identified for relay?

The regex set and the spaced-path/QUOTED_SEGMENT handling below are a
direct port of claude-plugin/pabel/hooks/pabel_relay_hook.py (see
docs/phase2-engineering-notes.md sec 9 for the bugs already found and
fixed there) - nothing here was ever Claude-Code-specific, since it only
ever operates on the serialized tool_input, never on a Claude-only field
name, so the port is unchanged.
"""

import json
import re
from pathlib import Path

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


def mentions_target(tool_input):
    text = json.dumps(tool_input)
    return bool(ENCRYPTED_FILE.search(text) or DOCUMENTS_PATH.search(text))


def invokes_oabe_binary(tool_input):
    return bool(OABE_BINARY.search(json.dumps(tool_input)))


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
