"""Admin-only registry of AI agent products allowed to contribute
attributes to a combined ABE key (see core.agent_session_key).

This script is the *only* place an agent's attributes/required role are
set - never the running service, never the agent itself, mirroring how
only a Keycloak realm admin can set a user's attributes (see
server/README.md). Each agent product runs as its own container instance
(one PABEL_AGENT_ID per instance, see compose.yml) - that identity isn't
a per-request claim, so there's no separate credential to manage here,
just: which attributes does this agent contribute, and which Keycloak
realm role must a user's token carry to receive them.

Two different ways an agent can end up contributing nothing to a given
request, both deliberate (see core.resolve_agent()):
  - agent_id has no row here at all (or is disabled) -> hard error.
  - agent_id is registered, but the calling user's token lacks
    required_role -> soft: zero attributes contributed for that user,
    everyone else with the role is unaffected. This is what makes "block
    agent X for user Y, not everyone" just a role un-assignment in
    Keycloak - no code or registry change needed per user.

Usage:
  python agents_admin.py add AGENT_ID DISPLAY_NAME ATTRIBUTES REQUIRED_ROLE
      e.g. add claude-code "Claude Code" agent_claude_code agent_claude_code_user
      REQUIRED_ROLE must be a realm role that exists in Keycloak - assign
      it to whichever users should be allowed to use this agent.
  python agents_admin.py list
  python agents_admin.py enable AGENT_ID
  python agents_admin.py disable AGENT_ID
"""

import argparse
import sys

import db


def do_add(agent_id, display_name, attributes, required_role):
    db.add_agent(agent_id, display_name, attributes, required_role)
    print(f"agent {agent_id!r} ({display_name}) registered with attributes "
         f"{attributes!r}, gated by realm role {required_role!r}")
    print(f"Assign the {required_role!r} role (Keycloak admin console) to "
         "whichever users may use this agent.")


def do_list():
    rows = db.list_agents()
    if not rows:
        print("no agents registered")
        return
    for agent_id, display_name, attributes, enabled, created_at, updated_at in rows:
        status = "enabled" if enabled else "disabled"
        print(f"  {agent_id} ({display_name}) [{status}]: {attributes}  "
             f"created {created_at}  updated {updated_at}")


def do_set_enabled(agent_id, enabled):
    if not db.set_agent_enabled(agent_id, enabled):
        raise SystemExit(f"error: no such agent_id: {agent_id!r}")
    print(f"agent {agent_id!r} {'enabled' if enabled else 'disabled'}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("add", help="register (or update) an agent")
    p.add_argument("agent_id")
    p.add_argument("display_name")
    p.add_argument("attributes", help="'|'-joined ABE tokens, e.g. agent_claude_code")
    p.add_argument("required_role", help="Keycloak realm role users need to use this agent")

    sub.add_parser("list", help="show registered agents")

    p = sub.add_parser("enable", help="re-enable a disabled agent")
    p.add_argument("agent_id")
    p = sub.add_parser("disable", help="disable an agent immediately")
    p.add_argument("agent_id")

    args = parser.parse_args(argv)
    try:
        if args.command == "add":
            do_add(args.agent_id, args.display_name, args.attributes, args.required_role)
        elif args.command == "list":
            do_list()
        elif args.command == "enable":
            do_set_enabled(args.agent_id, True)
        elif args.command == "disable":
            do_set_enabled(args.agent_id, False)
    except Exception as e:
        raise SystemExit(f"error: {e}")


if __name__ == "__main__":
    sys.exit(main())
