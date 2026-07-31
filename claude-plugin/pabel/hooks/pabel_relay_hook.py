"""Thin glue for the Claude Code plugin's PreToolUse hook.

All detection/relay/policy logic now lives in the agent-agnostic
`pabel-connector` package (PABEL/connector/) - this script only dispatches
into it with the "claude-code" registry key, so Claude Code gets exactly
the same behavior as before the refactor (see
PABEL/connector/src/pabel_connector/adapters/claude_code.py, the VERIFIED
reference adapter) without duplicating any of it here. See
docs/phase2-engineering-notes.md sec 9-10 for why this moved out of the
plugin: a second, forever-diverging copy of the same logic was worse than
one shared core every agent's adapter builds on.
"""

import sys

from pabel_connector.hook import main

if __name__ == "__main__":
    sys.exit(main(["claude-code"]))
