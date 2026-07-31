"""Cursor sessionStart hook: inject PABEL env vars for hook subprocesses."""
import json
import sys

print(json.dumps({
    "env": {
        "PABEL_SERVER_URL": "http://localhost:8001/mcp",
        "PABEL_KEYCLOAK_URL": "http://localhost:8080",
        "PABEL_KEYCLOAK_REALM": "pabel",
        "PABEL_KEYCLOAK_CLIENT_ID": "pabel",
        "PABEL_CALLBACK_PORT": "8766",
    }
}))
sys.exit(0)
