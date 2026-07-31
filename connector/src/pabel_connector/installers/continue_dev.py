"""Continue.dev - no installer, no pre-tool-use hook primitive exists at
all in Continue (only a static allow/ask/disable ToolPolicy and a CLI
permissions.yaml) - so there is no way to transparently substitute a
relayed result for a blocked call the way every other adapter in this
package does. See connector/docs/known-gaps.md.
"""

from pathlib import Path

name = "continue-dev"
status = "gap"


def required_env():
    return []


def install(base_dir: Path) -> str:
    return (
        "No enforcement adapter for Continue.dev: it has no pre-tool-use hook "
        "primitive at all (only a static allow/ask/disable tool policy) - so "
        "there's no way to transparently substitute a relayed result the way "
        "every other agent in this package does. See connector/docs/known-gaps.md.\n"
        "What IS available: register the deployed PABEL server as a normal MCP "
        "server in Continue's own config (whoami/read_document become callable "
        "tools, same as any MCP client) - ask your admin for the server URL and "
        "add it under Continue's MCP servers settings. There is no blocking of "
        "direct .abe reads for this agent."
    )
