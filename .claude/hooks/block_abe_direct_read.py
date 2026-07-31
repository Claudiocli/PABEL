"""PreToolUse hook: block local decryption of ABE-encrypted project files.

read_document (server/mcp_server.py) now takes the document's raw text as
an argument, not a server-side path: the agent is expected to read a
*.abe file itself and hand its content to the tool (see mcp_server.py's
docstring) - that file is ciphertext-only at rest (policies are already
visible to every caller by design, per document.py), so reading it does
not, by itself, expose anything the access-control model is meant to
protect. What must still never happen locally is decryption itself: that
combines the user's *and* the agent's attributes into one key
server-side (core.agent_session_key) and is audited - a local
oabe_dec/oabe_keygen/oabe_setup call bypasses both entirely.

So this only blocks Bash commands that invoke OpenABE's CLI binaries
directly - never Read/Grep, and never a plain cat/type of an *.abe file,
which are exactly how content reaches read_document now. Calls to this
project's own service scripts are always allowed regardless of what they
mention.

Non-matching calls exit 0 immediately without printing anything, so this
adds no overhead or noise for any other file in the project.
"""

import json
import re
import sys

OABE_BINARY = re.compile(r"(?i)\boabe_(setup|keygen|enc|dec)\b")

# Bash commands that are always fine regardless of what they mention: this
# project's own service scripts.
SAFE_BASH = [
    re.compile(r"(?i)\b(mcp_server|core|login|db|agents_admin)\.py\b"),
]

MESSAGE = (
    "This invokes an OpenABE CLI binary directly. Decryption/keygen must "
    "happen inside the 'pabel' MCP server (server/core.py), which combines "
    "the current user's *and* the calling agent's attributes into one key "
    "and audits the result - calling oabe_dec/oabe_keygen/oabe_setup "
    "directly would bypass both."
)


def main():
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return 0  # malformed/absent input: nothing to block on

    tool_name = payload.get("tool_name")
    tool_input = payload.get("tool_input") or {}

    if tool_name != "Bash":
        return 0

    command = tool_input.get("command") or ""
    blocked = bool(OABE_BINARY.search(command)) and not any(
        p.search(command) for p in SAFE_BASH)

    if blocked:
        print(MESSAGE, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
