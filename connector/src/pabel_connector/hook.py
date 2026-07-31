"""Console-script `pabel-connector-hook <key>`: the one runtime glue every
agent's own hook command actually invokes. `<key>` identifies which
registered Adapter to dispatch to (see registry.py) - reading stdin,
calling core.decide, writing the adapter's rendered response, and
returning its exit code are all identical regardless of agent; only
parse()/render() differ.
"""

import sys

from .core.decide import decide
from .registry import ADAPTERS


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        sys.stderr.write("usage: pabel-connector-hook <agent-key> [adapter-args...]\n")
        return 2
    key, rest = argv[0], argv[1:]
    adapter = ADAPTERS.get(key)
    if adapter is None:
        sys.stderr.write(f"pabel-connector-hook: unknown agent key {key!r}\n")
        return 2

    stdin_bytes = sys.stdin.buffer.read()
    call = adapter.parse(rest, stdin_bytes)
    response = adapter.render(decide(call))
    if response.stdout:
        sys.stdout.write(response.stdout)
    if response.stderr:
        sys.stderr.write(response.stderr)
    return response.exit_code


if __name__ == "__main__":
    sys.exit(main())
