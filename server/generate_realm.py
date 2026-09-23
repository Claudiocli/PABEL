"""Fill realm-org.template.json with the secrets from .env.

Keycloak's --import-realm reads a plain JSON file, so these values cannot
be injected as container environment variables; this script does the
substitution once, before `nerdctl compose up`, so the real
realm-org.json (with real secrets) never has to be a template and never
has to be committed.

Two kinds of secret get substituted. The demo users' passwords, and one
client_secret per agent installation the template pre-provisions.

That second kind exists because Keycloak runs `start-dev` here, keeping
its database inside the container with no volume: removing the container
destroys every client agents_admin.py created at runtime, while the
agent_installations rows in Postgres survive on their own volume and go
on pointing at clients that no longer exist. Declaring the demo
installations in the template makes the two stores agree again after any
restart - at the cost of a fixed, .env-held secret, which is acceptable
for a local demo and is not how a real deployment should work (there,
installations are created at runtime, one per employee, and no
client_secret belongs in a file at all).

Usage:  python generate_realm.py
"""

import re
from pathlib import Path

import env

SERVICE_DIR = Path(__file__).resolve().parent
TEMPLATE = SERVICE_DIR / "realm-org.template.json"
OUTPUT = SERVICE_DIR / "realm-org.json"

PLACEHOLDERS = (
    "ALICE_PASSWORD", "BOB_PASSWORD", "CHARLIE_PASSWORD",
    "CLAUDE_CODE_INSTALLATION_SECRET", "OPENCODE_INSTALLATION_SECRET",
)


def main():
    values = dict(zip(PLACEHOLDERS, env.require(*PLACEHOLDERS)))
    text = TEMPLATE.read_text(encoding="utf-8")
    for name, value in values.items():
        text = text.replace("{{%s}}" % name, value)

    # A placeholder this script doesn't know about would otherwise reach
    # Keycloak literally - a client whose secret is the string
    # "{{SOMETHING}}", which authenticates nothing and fails much later,
    # as an unexplained 401.
    left_over = sorted(set(re.findall(r"\{\{(\w+)\}\}", text)))
    if left_over:
        raise SystemExit(
            f"error: no value for {', '.join(left_over)} - add each one to "
            f"PLACEHOLDERS in {Path(__file__).name} and to server/.env")

    OUTPUT.write_text(text, encoding="utf-8")
    print(f"{OUTPUT} generated from {TEMPLATE.name} and server/.env")


if __name__ == "__main__":
    main()
