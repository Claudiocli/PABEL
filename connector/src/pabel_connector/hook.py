"""Console-script `pabel-connector-hook <key>`: the one runtime glue every
agent's own hook command actually invokes. `<key>` identifies which
registered Adapter to dispatch to (see registry.py) - reading stdin,
calling core.decide, writing the adapter's rendered response, and
returning its exit code are all identical regardless of agent; only
parse()/render() differ.
"""

import sys

from .core.decide import decide
from .registry import ADAPTERS, SESSION_END_HANDLERS


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        sys.stderr.write("usage: pabel-connector-hook <agent-key> [adapter-args...]\n")
        return 2
    key, rest = argv[0], argv[1:]

    # SessionEnd isn't a tool call - no NormalizedCall/Decision fits it, so it
    # bypasses decide()/ADAPTERS entirely via its own small dispatch table.
    session_end_handler = SESSION_END_HANDLERS.get(key)
    if session_end_handler is not None:
        stdin_bytes = sys.stdin.buffer.read()
        response = session_end_handler(stdin_bytes)
        if response.stdout:
            sys.stdout.write(response.stdout)
        if response.stderr:
            sys.stderr.write(response.stderr)
        return response.exit_code

    adapter = ADAPTERS.get(key)
    if adapter is None:
        sys.stderr.write(f"pabel-connector-hook: unknown agent key {key!r}\n")
        return 2
    # Multi-hook-point agents register several keys, one per hook point,
    # formatted "<agent>:<hookpoint>" (see registry.py) - the agent_id this
    # installation was enrolled under (agent_session.py's storage key) is
    # always just the part before the colon, single-hook-point agents
    # included (their key already equals their plain agent_id).
    agent_id = key.split(":", 1)[0]

    stdin_bytes = sys.stdin.buffer.read()
    call = adapter.parse(rest, stdin_bytes)
    response = adapter.render(decide(call, agent_id))
    if response.stdout:
        sys.stdout.write(response.stdout)
    if response.stderr:
        sys.stderr.write(response.stderr)
    return response.exit_code


if __name__ == "__main__":
    sys.exit(main())
