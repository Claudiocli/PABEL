"""Admin-only registry of AI agent products, and of their individual
installations, allowed to contribute attributes to a combined ABE key (see
core.agent_session_key).

This script is the *only* place an agent's attributes/required role - or a
specific installation's right to act as that agent - are set, never the
running service, never the agent itself, mirroring how only a Keycloak
realm admin can set a user's attributes (see server/README.md). One
shared server instance serves every agent product (server/compose.yml) -
"which agent is calling" is proven per request by a real Keycloak
client_credentials token (core.resolve_agent()), never inferred from
deployment topology. Creating that credential is exclusively an admin
action, run locally with the admin's own Keycloak admin session: nothing
here is ever reachable over the network from an employee's own machine.
The employee only ever *receives* a client_id/client_secret an admin
already created (out of band - e.g. the same channel used to hand out
their own Keycloak login) and stores it locally via
`pabel-connector install <agent> --client-id ... --client-secret ...`.

Two different ways an agent can end up contributing nothing to a given
request, both deliberate (see core.resolve_agent()):
  - the installation's client_id has no row here at all, is revoked, or
    its agent_id product is disabled -> hard error.
  - the installation is valid, but the calling user's token lacks the
    product's required_role -> soft: zero attributes contributed for that
    user, everyone else with the role is unaffected. This is what makes
    "block agent X for user Y, not everyone" just a role un-assignment in
    Keycloak - no code or registry change needed per user.

Usage:
  python agents_admin.py add AGENT_ID DISPLAY_NAME ATTRIBUTES REQUIRED_ROLE
      e.g. add claude-code "Claude Code" agent_claude_code agent_claude_code_user
      REQUIRED_ROLE must be a realm role that exists in Keycloak - assign
      it to whichever users should be allowed to use this agent.
  python agents_admin.py list
  python agents_admin.py enable AGENT_ID
  python agents_admin.py disable AGENT_ID

  python agents_admin.py create-installation AGENT_ID [--label TEXT]
      Creates a new Keycloak client_credentials client + service account
      for one specific installation of AGENT_ID, and registers it. Prints
      client_id/client_secret ONCE - hand them to the employee out of
      band; they cannot be recovered afterward (only rotated, by revoking
      and creating a new installation).
  python agents_admin.py list-installations [AGENT_ID]
  python agents_admin.py revoke-installation CLIENT_ID
  python agents_admin.py enable-installation CLIENT_ID
"""

import argparse
import secrets
import sys

import requests

import db
import env


def _admin_token():
    """Password grant against admin-cli in the master realm - same
    bootstrap admin credentials and pattern setup_user_profile.py already
    uses. Only ever called from this admin-run script, never from the
    running MCP server."""
    base_url, = env.require("KEYCLOAK_URL")
    admin_user, admin_pass = env.require(
        "KC_BOOTSTRAP_ADMIN_USERNAME", "KC_BOOTSTRAP_ADMIN_PASSWORD")
    resp = requests.post(
        f"{base_url}/realms/master/protocol/openid-connect/token",
        data={"grant_type": "password", "client_id": "admin-cli",
             "username": admin_user, "password": admin_pass},
        timeout=10)
    resp.raise_for_status()
    return resp.json()["access_token"]


def _create_keycloak_client(agent_id, label):
    """Create a new confidential client with its service account enabled -
    this installation's own Keycloak identity (client_credentials grant
    only; no browser flow, no direct password grant). Returns
    (client_id, client_secret)."""
    base_url, realm = env.require("KEYCLOAK_URL", "REALM")
    headers = {"Authorization": f"Bearer {_admin_token()}"}
    client_id = f"pabel-agent-{agent_id}-{secrets.token_hex(8)}"
    resp = requests.post(
        f"{base_url}/admin/realms/{realm}/clients", headers=headers,
        json={
            "clientId": client_id,
            "name": label or f"{agent_id} installation",
            "protocol": "openid-connect",
            "publicClient": False,
            "standardFlowEnabled": False,
            "directAccessGrantsEnabled": False,
            "serviceAccountsEnabled": True,
        }, timeout=10)
    resp.raise_for_status()
    secret_resp = requests.get(
        f"{resp.headers['Location']}/client-secret", headers=headers, timeout=10)
    secret_resp.raise_for_status()
    return client_id, secret_resp.json()["value"]


def _set_keycloak_client_enabled(client_id, enabled):
    """Best-effort defense in depth: find this client by its clientId and
    flip `enabled`. Postgres (db.set_installation_revoked) is the fast,
    authoritative check core.resolve_agent() actually relies on - this
    only additionally prevents the installation from obtaining any *new*
    token; a still-unexpired one it already holds is unaffected, same as
    any OAuth revocation."""
    base_url, realm = env.require("KEYCLOAK_URL", "REALM")
    headers = {"Authorization": f"Bearer {_admin_token()}"}
    found = requests.get(
        f"{base_url}/admin/realms/{realm}/clients",
        headers=headers, params={"clientId": client_id}, timeout=10).json()
    if not found:
        raise RuntimeError(f"no Keycloak client found for clientId {client_id!r}")
    resp = requests.put(
        f"{base_url}/admin/realms/{realm}/clients/{found[0]['id']}",
        headers=headers, json={**found[0], "enabled": enabled}, timeout=10)
    resp.raise_for_status()


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


def do_create_installation(agent_id, label):
    if db.get_agent(agent_id) is None:
        raise SystemExit(f"error: no such agent product: {agent_id!r} - "
                         "register it first with 'add'")
    client_id, client_secret = _create_keycloak_client(agent_id, label)
    db.add_agent_installation(client_id, agent_id, label)
    print(f"installation created for agent {agent_id!r}:")
    print(f"  client_id:     {client_id}")
    print(f"  client_secret: {client_secret}")
    print("This secret is shown ONCE and cannot be recovered later (only "
         "rotated, by revoking this installation and creating a new one).")
    print("Hand both values to the employee out of band, for them to run:")
    print(f"  pabel-connector install {agent_id} "
         f"--client-id {client_id} --client-secret <secret above>")


def do_list_installations(agent_id):
    rows = db.list_agent_installations(agent_id)
    if not rows:
        print("no installations registered" + (f" for {agent_id!r}" if agent_id else ""))
        return
    for client_id, aid, label, revoked, enrolled_at, revoked_at in rows:
        status = "revoked" if revoked else "active"
        label_part = f" ({label})" if label else ""
        print(f"  {client_id} [{aid}]{label_part} [{status}]  enrolled {enrolled_at}")


def do_set_installation_revoked(client_id, revoked):
    if not db.set_installation_revoked(client_id, revoked):
        raise SystemExit(f"error: no such installation: {client_id!r}")
    try:
        _set_keycloak_client_enabled(client_id, not revoked)
    except Exception as e:
        print(f"warning: Postgres is updated (takes effect on this installation's "
             f"very next request), but updating it in Keycloak failed: {e} - "
             "do so manually in the admin console as defense in depth.")
    print(f"installation {client_id!r} {'revoked' if revoked else 're-enabled'}")


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

    p = sub.add_parser("create-installation",
                       help="create a new Keycloak client_credentials client "
                            "for one installation of an agent product")
    p.add_argument("agent_id")
    p.add_argument("--label", default=None, help="free text, e.g. a hostname")

    p = sub.add_parser("list-installations", help="show registered installations")
    p.add_argument("agent_id", nargs="?", default=None)

    p = sub.add_parser("revoke-installation", help="revoke one installation immediately")
    p.add_argument("client_id")
    p = sub.add_parser("enable-installation", help="re-enable a revoked installation")
    p.add_argument("client_id")

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
        elif args.command == "create-installation":
            do_create_installation(args.agent_id, args.label)
        elif args.command == "list-installations":
            do_list_installations(args.agent_id)
        elif args.command == "revoke-installation":
            do_set_installation_revoked(args.client_id, True)
        elif args.command == "enable-installation":
            do_set_installation_revoked(args.client_id, False)
    except Exception as e:
        raise SystemExit(f"error: {e}")


if __name__ == "__main__":
    sys.exit(main())
