"""This dev repo's own PreToolUse hook - a thin dispatcher into the
agent-agnostic `pabel-connector` package (PABEL/connector/), the same
`pabel_connector.hook` module `installers/claude_code.py` now wires up for
every real install via `pabel-connector install claude-code --dir .`; this
file predates that installer and hasn't been migrated to it yet, though
the logic it dispatches to is identical either way.

Superseded `block_abe_direct_read.py` (which only ever blocked a direct
`oabe_*` CLI invocation via Bash - Read/Grep/Bash-cat on an .abe file went
straight through). That was correct for the architecture at the time (the
model had to read the raw file itself to hand its content to
read_document), but the project moved past that: the hook itself now does
the relay, so the model reading raw ciphertext directly is no longer
necessary, and blocking every access method - not just the CLI binary
call - is both possible and the actual current design (see
docs/phase2-engineering-notes.md sec 9-10). This closes that gap for this
repo's own Claude Code session, matching what the plugin already
enforces for a real install.
"""

import sys

from pabel_connector.hook import main

if __name__ == "__main__":
    sys.exit(main(["claude-code"]))
