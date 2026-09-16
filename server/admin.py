"""One entry point for every administrative action on a PABEL deployment.

Before this, standing up a deployment meant running generate_realm.py,
`nerdctl compose up`, setup_user_profile.py, db.init_schema(),
abe.setup_authority() and then agents_admin.py, in that order, and creating
each agent's realm role by hand in the Keycloak console in between. The
order is not obvious and getting it wrong fails quietly: the authority step
in particular used to be documented as a host command, which produced an
authority the containerised server could not read at all (`caught exception:
Invalid function input`) while `authority_exists()` still reported True,
because that only checks the public mpk.

So this script is not a convenience wrapper. Each command here encodes an
ordering or a cross-component invariant that was previously carried in an
admin's head:

  bootstrap  - the whole first-time sequence, with the ABE authority created
               INSIDE the container, by the same OpenABE build that will
               later read it.
  onboard    - an agent product end to end: database row, Keycloak realm
               role (created here, not in the console), optional role
               assignment, and one installation - ending in the single line
               the employee has to copy.
  teardown   - the reverse, including the two stores that are easy to forget
               and that silently break a fresh install if they survive it:
               the agent_keys cache and the client-side credential file.
  status     - what actually exists right now, across all four stores.

agents_admin.py remains the place for per-installation lifecycle
(revoke/enable, audit keypairs); this script calls into it rather than
duplicating its Keycloak client creation.
"""

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import requests

import agents_admin
import db
import env

SERVER_DIR = Path(__file__).resolve().parent
AUTHORITY_DIR = SERVER_DIR / "authority"
REALM_FILE = SERVER_DIR / "realm-org.json"

# Rancher Desktop with the containerd engine: the docker CLI is not present
# at all on this deployment's machines (see README).
COMPOSE = ["nerdctl", "compose"]

DEMO_USERS = ("alice", "bob", "charlie")


# --------------------------------------------------------------------------
# plumbing


def _compose(*args, check=True):
    return subprocess.run(COMPOSE + list(args), cwd=SERVER_DIR, check=check)


def _say(step, message):
    print(f"[{step}] {message}", flush=True)


def _attributes_for(agent_id):
    """The naming convention agents_admin.py documents: an agent product
    contributes one attribute and is gated by one realm role, both derived
    from its id. Derived rather than asked for, so the two can never drift
    apart between the database row and Keycloak."""
    stem = agent_id.replace("-", "_")
    return f"agent_{stem}", f"agent_{stem}_user"


def _wait_for_keycloak(timeout=180):
    base_url, realm = env.require("KEYCLOAK_URL", "REALM")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if requests.get(f"{base_url}/realms/{realm}", timeout=5).status_code == 200:
                return
        except requests.RequestException:
            pass
        time.sleep(3)
    raise RuntimeError(
        f"Keycloak did not serve realm {realm!r} at {base_url} within {timeout}s - "
        f"check `nerdctl compose logs keycloak`")


def _admin_headers():
    return {"Authorization": f"Bearer {agents_admin._admin_token()}"}


def _ensure_realm_role(role):
    """Create the realm role if it isn't there. Previously do_add() only
    *printed* a reminder to create it in the console, which is precisely the
    kind of step that gets skipped - and an agent whose required_role does
    not exist contributes no attributes to anyone, silently."""
    base_url, realm = env.require("KEYCLOAK_URL", "REALM")
    resp = requests.post(f"{base_url}/admin/realms/{realm}/roles",
                         headers=_admin_headers(), json={"name": role}, timeout=10)
    if resp.status_code == 409:
        return False
    resp.raise_for_status()
    return True


def _assign_realm_role(username, role):
    base_url, realm = env.require("KEYCLOAK_URL", "REALM")
    headers = _admin_headers()
    users = requests.get(f"{base_url}/admin/realms/{realm}/users", headers=headers,
                         params={"username": username, "exact": "true"},
                         timeout=10).json()
    if not users:
        raise RuntimeError(f"no Keycloak user {username!r} in realm {realm!r}")
    role_rep = requests.get(f"{base_url}/admin/realms/{realm}/roles/{role}",
                            headers=headers, timeout=10)
    role_rep.raise_for_status()
    resp = requests.post(
        f"{base_url}/admin/realms/{realm}/users/{users[0]['id']}/role-mappings/realm",
        headers=headers, json=[role_rep.json()], timeout=10)
    resp.raise_for_status()


# --------------------------------------------------------------------------
# commands


def cmd_bootstrap(args):
    env.load()
    env.require("KEYCLOAK_URL", "REALM", "KC_BOOTSTRAP_ADMIN_USERNAME",
                "KC_BOOTSTRAP_ADMIN_PASSWORD", "POSTGRES_USER", "POSTGRES_PASSWORD",
                "POSTGRES_DB", "ALICE_PASSWORD", "BOB_PASSWORD", "CHARLIE_PASSWORD")

    _say("1/6", "generating realm-org.json from the template and .env")
    import generate_realm
    generate_realm.main()

    if not args.skip_compose:
        _say("2/6", "starting Keycloak, Postgres and the MCP server")
        _compose("up", "-d")
    else:
        _say("2/6", "skipped (--skip-compose)")

    _say("3/6", "waiting for Keycloak to serve the realm")
    _wait_for_keycloak()

    _say("4/6", "declaring abe_attributes on the user profile (admin-edit only)")
    import setup_user_profile
    setup_user_profile.main()

    _say("5/6", "creating the database schema")
    db.init_schema()

    # The step this whole script exists for. abe.setup_authority() run on the
    # host uses the host's OpenABE build; the server only ever runs the
    # container's. Those two serialise the master key differently, and the
    # mismatch surfaces far away, as a keygen failure on first document read.
    #
    # `compose exec -T`, not `compose run`: nerdctl's compose run always
    # allocates a TTY (it has no -T) and dies with "provided file is not a
    # console" when driven from a script, and it starts a second copy of
    # every depends_on service on the way. exec reuses the container that is
    # already up and leaves nothing behind.
    _say("6/6", "creating the ABE authority INSIDE the container")
    _compose("exec", "-T", "mcp-server",
             "python", "-c", "import abe; abe.setup_authority()")

    _say("done", "deployment is up. Next: `python admin.py onboard <agent-id>`")
    return 0


def cmd_onboard(args):
    env.load()
    agent_id = args.agent_id
    attributes, required_role = _attributes_for(agent_id)
    display_name = args.display_name or agent_id

    db.add_agent(agent_id, display_name, attributes, required_role)
    _say("1/4", f"registered {agent_id!r} with attribute {attributes!r}")

    created = _ensure_realm_role(required_role)
    _say("2/4", f"realm role {required_role!r} "
                f"{'created' if created else 'already existed'}")

    granted = [u for u in (args.grant_to or "").split(",") if u.strip()]
    for username in granted:
        _assign_realm_role(username.strip(), required_role)
    _say("3/4", f"granted to: {', '.join(granted) if granted else '(nobody yet)'}")

    client_id, client_secret = agents_admin._create_keycloak_client(agent_id, args.label)
    db.add_agent_installation(client_id, agent_id, args.label)
    _say("4/4", "installation created")

    # The single line the employee copies. Printed last and alone so it is
    # not lost in the log above, and never stored anywhere by this script:
    # Keycloak will not show this secret again.
    print("\n" + "=" * 72)
    print("Send this ONE line to whoever installs the agent (secret shown once):\n")
    print(f"  pabel-connector install {agent_id} "
          f"--client-id {client_id} --client-secret {client_secret}")
    print("=" * 72)
    if not granted:
        print(f"\nNobody holds {required_role!r} yet, so nobody can use this agent.")
        print(f"Grant it with: python admin.py grant {agent_id} --to alice,bob")
    return 0


def cmd_grant(args):
    env.load()
    _, required_role = _attributes_for(args.agent_id)
    for username in [u.strip() for u in args.to.split(",") if u.strip()]:
        _assign_realm_role(username, required_role)
        print(f"granted {required_role!r} to {username!r}")
    return 0


def cmd_status(args):
    env.load()
    # flush before handing the terminal to a subprocess, or this header
    # lands after the output it introduces.
    print("containers:", flush=True)
    subprocess.run(["nerdctl", "ps", "--format",
                    "  {{.Names}}  {{.Status}}  {{.Ports}}"], check=False)

    print("\nABE authority:")
    for name in ("org.mpk.cpabe", "org.msk.cpabe"):
        path = AUTHORITY_DIR / name
        print(f"  {name}: {'%d bytes' % path.stat().st_size if path.exists() else 'MISSING'}")

    print("\ndatabase:")
    try:
        with db.connect() as conn:
            for table in ("agents", "agent_installations", "agent_keys", "audit_log"):
                count = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                print(f"  {table:22s} {count}")
    except Exception as e:
        print(f"  unreachable: {e}")

    print("\nagents:")
    for agent_id, _display_name, attributes, enabled, *_ in db.list_agents():
        # required_role comes from the row, not from _attributes_for(): an
        # agent registered with a role outside the convention would otherwise
        # be reported with the role it *should* have rather than the one that
        # actually gates it.
        required_role = (db.get_agent(agent_id) or {}).get("required_role", "?")
        state = "enabled " if enabled else "DISABLED"
        print(f"  {state}  {agent_id:18s} {attributes:24s} role={required_role}")

    # (client_id, agent_id, label, revoked, enrolled_at, revoked_at) - revoked
    # is the fourth field, before the timestamps, not after them.
    print("\ninstallations:")
    for client_id, agent_id, label, revoked, *_ in db.list_agent_installations():
        state = "REVOKED " if revoked else "active  "
        print(f"  {state}  {agent_id:18s} {client_id}  {label or ''}")
    return 0


def cmd_teardown(args):
    env.load()
    if not args.yes:
        print("This destroys, irreversibly:")
        print("  - the Postgres volume (agents, installations, agent_keys, audit_log)")
        print("  - the Keycloak realm and every agent installation client in it")
        print("  - the ABE authority: EVERY existing encrypted document becomes")
        print("    permanently unreadable, including any this deployment did not create")
        if args.include_client:
            print("  - this machine's own agent credentials and installed agent wiring")
        print("\nRe-run with --yes to proceed.")
        return 1

    _say("1/4", "stopping containers and removing the Postgres volume")
    _compose("down", "-v", check=False)

    _say("2/4", "removing the ABE authority and the generated realm")
    for path in list(AUTHORITY_DIR.glob("*.cpabe")) + [REALM_FILE]:
        if path.exists():
            path.unlink()
            print(f"  removed {path}")

    if args.include_client:
        _say("3/4", "removing this machine's client-side state")
        _teardown_client()
    else:
        _say("3/4", "client-side state kept (--include-client to remove it)")

    _say("4/4", "done. Run `python admin.py bootstrap` for a clean deployment.")
    return 0


def _teardown_client():
    """The client half of a clean install. Kept here rather than left to the
    admin because a surviving ~/.pabel/agent_credentials.json points at
    Keycloak clients that no longer exist, and surviving agent wiring keeps
    invoking a hook for an installation the server has never heard of -
    neither is visible from the server side at all.

    Machine-wide state only. `pabel-connector uninstall` now defaults to the
    user-level location, and that is the right scope here: an agent config
    inside a *project* may well be version-controlled - this repo's own
    .claude/, .cursor/, .github/hooks/ and .vscode/ wiring all is - so
    deleting it would destroy tracked source rather than clean a machine.
    Resetting that is a git operation, deliberately not a teardown one."""
    connector = shutil.which("pabel-connector")
    credentials = Path.home() / ".pabel" / "agent_credentials.json"

    agents = []
    if credentials.exists():
        try:
            agents = sorted(json.loads(credentials.read_text(encoding="utf-8")))
        except ValueError:
            pass

    if connector:
        for agent_id in agents:
            subprocess.run([connector, "uninstall", agent_id], check=False)
    elif agents:
        print("  pabel-connector is not on PATH - remove the agent wiring by hand:")
        for agent_id in agents:
            print(f"    pabel-connector uninstall {agent_id}")

    for path in (credentials, Path.home() / ".pabel" / "session.json"):
        if path.exists():
            path.unlink()
            print(f"  removed {path}")


# --------------------------------------------------------------------------
# interactive menu


MENU = """
  1  bootstrap   stand up a deployment (realm, containers, schema, authority)
  2  onboard     register an agent + create one installation
  3  grant       give an agent's realm role to users
  4  status      what exists right now
  5  teardown    destroy everything (irreversible)
  0  exit
"""


def _ask(prompt, default=None):
    suffix = f" [{default}]" if default else ""
    answer = input(f"{prompt}{suffix}: ").strip()
    return answer or default


def cmd_menu(_args):
    """Interactive front end. The commands below are the same ones the
    subcommands run - this only removes the need to remember which flag
    belongs to which step, which is the whole reason this script exists."""
    actions = {
        "1": lambda: cmd_bootstrap(argparse.Namespace(skip_compose=False)),
        "2": _menu_onboard,
        "3": _menu_grant,
        "4": lambda: cmd_status(None),
        "5": _menu_teardown,
    }
    while True:
        print("\n" + "=" * 72)
        print("PABEL deployment admin")
        print("=" * 72 + MENU)
        choice = input("> ").strip()
        if choice in ("0", "q", ""):
            return 0
        action = actions.get(choice)
        if action is None:
            print(f"'{choice}' is not one of the options.")
            continue
        try:
            action()
        except KeyboardInterrupt:
            print("\ninterrupted - nothing further was done")
        except Exception as e:
            # A failed step must not drop the admin back to a bare shell
            # halfway through a sequence: the menu is where they can see
            # what state things are in and retry the right step.
            print(f"\n[!!] {type(e).__name__}: {e}")


def _menu_onboard():
    agent_id = _ask("agent id (e.g. opencode)")
    if not agent_id:
        return
    cmd_onboard(argparse.Namespace(
        agent_id=agent_id,
        display_name=_ask("display name", agent_id),
        label=_ask("label - which machine/employee", None),
        grant_to=_ask("grant its role to (comma-separated, blank for none)", None)))


def _menu_grant():
    agent_id = _ask("agent id")
    to = _ask("usernames (comma-separated)")
    if agent_id and to:
        cmd_grant(argparse.Namespace(agent_id=agent_id, to=to))


def _menu_teardown():
    cmd_teardown(argparse.Namespace(yes=False, include_client=False))
    include_client = _ask("also wipe this machine's credentials and agent wiring? (y/N)", "n")
    # A typed word rather than y/N: this is the one action in the script that
    # cannot be undone, and every existing encrypted document dies with it.
    if _ask("type DESTROY to confirm, anything else to cancel") != "DESTROY":
        print("cancelled - nothing was touched")
        return
    cmd_teardown(argparse.Namespace(
        yes=True, include_client=include_client.lower().startswith("y")))


def build_parser():
    parser = argparse.ArgumentParser(
        prog="admin.py", description="Administer a PABEL deployment end to end.")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("bootstrap", help="stand up a deployment, in the right order")
    p.add_argument("--skip-compose", action="store_true",
                   help="assume Keycloak/Postgres are already running")
    p.set_defaults(func=cmd_bootstrap)

    p = sub.add_parser("onboard", help="register an agent and create one installation")
    p.add_argument("agent_id", help="e.g. claude-code, opencode")
    p.add_argument("--display-name", default=None)
    p.add_argument("--label", default=None, help="which machine/employee this is for")
    p.add_argument("--grant-to", default=None,
                   help="comma-separated usernames to grant the agent's realm role to")
    p.set_defaults(func=cmd_onboard)

    p = sub.add_parser("grant", help="grant an agent's realm role to users")
    p.add_argument("agent_id")
    p.add_argument("--to", required=True, help="comma-separated usernames")
    p.set_defaults(func=cmd_grant)

    p = sub.add_parser("status", help="what exists right now, across all four stores")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("teardown", help="destroy the deployment (irreversible)")
    p.add_argument("--yes", action="store_true", help="skip the confirmation")
    p.add_argument("--include-client", action="store_true",
                   help="also remove this machine's credentials and agent wiring")
    p.set_defaults(func=cmd_teardown)

    p = sub.add_parser("menu", help="interactive menu (the default with no arguments)")
    p.set_defaults(func=cmd_menu)

    parser.set_defaults(func=cmd_menu)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
