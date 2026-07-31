"""Cline - no installer. Cline's hooks (`beforeTool`/`afterTool`, v3.36+)
are implemented as a plugin SDK (JS/TS, loaded into Cline's own runtime),
not a simple external-command JSON config like every other agent in this
package - and, as of this writing, hooks are explicitly macOS/Linux only,
with no Windows support at all. Since employee machines can't be assumed
non-Windows, this is out of scope this phase - see
connector/docs/known-gaps.md. Revisit once/if Windows support ships.
"""

from pathlib import Path

name = "cline"
status = "gap"


def required_env():
    return []


def install(base_dir: Path) -> str:
    return (
        "No adapter for Cline yet: its hooks are a JS/TS plugin SDK (not a "
        "simple external-command config) and are explicitly macOS/Linux-only "
        "today - no Windows support. See connector/docs/known-gaps.md.\n"
        "Cline can still be pointed at the PABEL MCP server directly (whoami/"
        "read_document tools, no enforcement) the same way any MCP client can - "
        "ask your admin for the deployed server's connection details."
    )
