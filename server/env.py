"""Minimal .env loader (no external dependency).

Reads KEY=VALUE lines from server/.env into os.environ, without
overwriting variables already set in the real environment (so a value
exported by the shell, or injected by an MCP client config, always
wins over the file).
"""

import os
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parent / ".env"


def load(path=ENV_PATH):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def require(*names):
    """Return the values of the given env vars, or raise if any is missing."""
    load()
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        raise RuntimeError(
            f"missing environment variable(s): {', '.join(missing)} "
            f"(set them in server/.env or in the real environment)")
    return [os.environ[n] for n in names]
