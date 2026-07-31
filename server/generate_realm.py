"""Fill realm-org.template.json with the passwords from .env.

Keycloak's --import-realm reads a plain JSON file, so the demo user
passwords cannot be injected as container environment variables; this
script does the substitution once, before `nerdctl compose up`, so the
real realm-org.json (with real secrets) never has to be a template and
never has to be committed.

Usage:  python generate_realm.py
"""

from pathlib import Path

import env

SERVICE_DIR = Path(__file__).resolve().parent
TEMPLATE = SERVICE_DIR / "realm-org.template.json"
OUTPUT = SERVICE_DIR / "realm-org.json"


def main():
    alice, bob, charlie = env.require(
        "ALICE_PASSWORD", "BOB_PASSWORD", "CHARLIE_PASSWORD")
    text = TEMPLATE.read_text(encoding="utf-8")
    text = (text
            .replace("{{ALICE_PASSWORD}}", alice)
            .replace("{{BOB_PASSWORD}}", bob)
            .replace("{{CHARLIE_PASSWORD}}", charlie))
    OUTPUT.write_text(text, encoding="utf-8")
    print(f"{OUTPUT} generated from {TEMPLATE.name} and server/.env")


if __name__ == "__main__":
    main()
