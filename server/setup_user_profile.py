"""One-time setup: declare abe_attributes in the realm's User Profile, so
a Keycloak admin can see and change it as a real field in the admin
console, and so only an admin - never the user, never any agent - can
edit it.

Keycloak's --import-realm JSON does not accept the User Profile schema
directly: RealmRepresentation's own "attributes" field is a plain
string-to-string map for generic realm settings, unrelated to User
Profile (this was confirmed the hard way - an earlier version of
realm-org.template.json put the schema there and Keycloak refused to
start). The User Profile schema is only configurable through the Admin
REST API, so this has to run once after Keycloak is already up, not as
part of the realm import.

Usage: python setup_user_profile.py   (run once, after `nerdctl compose up -d`)
"""

import requests

import env

env.load()


def main():
    base_url, realm = env.require("KEYCLOAK_URL", "REALM")
    admin_user, admin_pass = env.require(
        "KC_BOOTSTRAP_ADMIN_USERNAME", "KC_BOOTSTRAP_ADMIN_PASSWORD")

    token = requests.post(
        f"{base_url}/realms/master/protocol/openid-connect/token",
        data={"grant_type": "password", "client_id": "admin-cli",
              "username": admin_user, "password": admin_pass},
        timeout=10).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    profile_url = f"{base_url}/admin/realms/{realm}/users/profile"
    profile = requests.get(profile_url, headers=headers, timeout=10).json()

    if any(a["name"] == "abe_attributes" for a in profile["attributes"]):
        print("abe_attributes already declared in the User Profile.")
        return

    profile["attributes"].append({
        "name": "abe_attributes",
        "displayName": "ABE attributes",
        "multivalued": True,
        "permissions": {"view": ["admin", "user"], "edit": ["admin"]},
    })
    resp = requests.put(profile_url, headers=headers, json=profile, timeout=10)
    resp.raise_for_status()
    print("abe_attributes added to the User Profile: visible to the user, "
         "editable only by an admin.")


if __name__ == "__main__":
    main()
