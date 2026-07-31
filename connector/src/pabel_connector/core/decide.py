"""The one agent-agnostic policy: given a NormalizedCall, decide whether it
touches a PABEL-protected .abe file and, if so, what should happen. Every
adapter's render() turns this same Decision into its own agent's response
shape - this function never sees or cares which agent produced the call.
"""

from ..pabel_client.keycloak_client import AuthError
from ..pabel_client.relay import RelayError, read_document
from .detection import (
    ENCRYPTED_FILE,
    DOCUMENTS_PATH,
    PABEL_MCP_SERVER_NAME,
    find_relayable_file,
    invokes_oabe_binary,
    mentions_target,
)
from .types import Decision, DecisionKind, NormalizedCall


def decide(call: NormalizedCall) -> Decision:
    if call.mcp_target and call.mcp_target[0] == PABEL_MCP_SERVER_NAME:
        return Decision(DecisionKind.ALLOW)

    if call.is_write:
        # Checked against write_target specifically, never against the
        # whole tool_input: a write's *content* legitimately mentioning a
        # protected path (writing documentation, for instance) must not be
        # confused with writing *to* one.
        target_path = call.write_target or ""
        if ENCRYPTED_FILE.search(target_path) or DOCUMENTS_PATH.search(target_path):
            return Decision(
                DecisionKind.DENY_MUTATING,
                reason="This project has no write/authoring path for .abe files - "
                       "read_document on the deployed PABEL server is the only "
                       "sanctioned operation.")
        return Decision(DecisionKind.ALLOW)

    if call.is_execute and invokes_oabe_binary(call.tool_input):
        return Decision(
            DecisionKind.DENY_OABE_BINARY,
            reason="This invokes an OpenABE CLI binary directly. Decryption must "
                   "happen on the deployed PABEL server, which combines the "
                   "current user's and agent's attributes into one key and "
                   "audits the result - calling oabe_dec/oabe_keygen/oabe_setup "
                   "directly would bypass both.")

    if not mentions_target(call.tool_input):
        return Decision(DecisionKind.ALLOW)

    target = find_relayable_file(call.tool_input)
    if target is None:
        return Decision(
            DecisionKind.DENY_AMBIGUOUS,
            reason="This targets an .abe file or the documents/ folder in a way "
                   "that doesn't name one concrete existing file (a directory, a "
                   "pattern, or several candidates) - relaying automatically "
                   "isn't possible here. Ask for one specific file by name.")

    try:
        result = read_document(str(target), target.name)
    except AuthError as e:
        return Decision(DecisionKind.DENY_AUTH_ERROR,
                         reason=f"Not authenticated to the PABEL server yet: {e}")
    except RelayError as e:
        return Decision(DecisionKind.DENY_RELAY_ERROR,
                         reason=f"Relay to the PABEL server failed: {e}")

    return Decision(
        DecisionKind.DENY_WITH_RELAY,
        reason=f"Direct reads of .abe files are blocked - the PABEL server's "
               f"read_document result for {target.name!r} is provided instead.",
        content=result)
