"""The one agent-agnostic policy: given a NormalizedCall, decide whether it
touches a PABEL-protected .abe file and, if so, what should happen. Every
adapter's render() turns this same Decision into its own agent's response
shape - this function never sees or cares which agent produced the call,
beyond agent_id itself: purely a local key selecting which of this
installation's own stored credentials to use (agent_session.py) - never a
value sent anywhere as a claimed identity. See server/core.py's
resolve_agent() for what the server actually trusts (a verified Keycloak
token, not this string).

On an otherwise-relayable read, the actual relay (including running an
interactive browser+MFA login itself if there's no valid human session
yet) is delegated to pabel_client/relay.py's read_document_with_login() -
the same guided "file -> login if needed -> MCP decrypts" flow that
mcp_local_server.py's directly-callable read_document tool also uses, so
neither a blocked file read nor a model calling the tool explicitly can
diverge in behavior or leave a model to guess the right order of steps
itself.
"""

from ..pabel_client import agent_session
from ..pabel_client.keycloak_client import AuthError
from ..pabel_client.relay import RelayError, read_document_with_login
from .detection import (
    ENCRYPTED_FILE,
    DOCUMENTS_PATH,
    PABEL_CONNECTOR_MCP_SERVER_NAME,
    PABEL_MCP_SERVER_NAME,
    find_relayable_file,
    invokes_oabe_binary,
    invokes_pabel_connector_internals,
    mentions_target,
    touches_pabel_credential_store,
)
from .types import Decision, DecisionKind, NormalizedCall


def decide(call: NormalizedCall, agent_id: str) -> Decision:
    # Checked before anything else, regardless of read/write/execute: this
    # installation's own local secrets (session.py/agent_session.py) are
    # never a legitimate target for the model itself, and none of the
    # checks below would otherwise catch a plain read of them.
    if touches_pabel_credential_store(call.tool_input):
        return Decision(
            DecisionKind.DENY_CREDENTIAL_ACCESS,
            reason="This targets PABEL's own local credential store (this "
                   "installation's human session or agent client secret/token) - "
                   "only this package's own code ever reads these; denied "
                   "unconditionally, never relayed. To check who is currently "
                   "logged in (or why a document section came back access-denied) "
                   "without reading any local file, call the pabel MCP server's own "
                   "whoami tool instead - it reports username, ABE attributes, and "
                   "authorization status directly, and is always a sanctioned call.")

    if call.is_execute and invokes_pabel_connector_internals(call.tool_input):
        return Decision(
            DecisionKind.DENY_HOOK_BYPASS,
            reason="This command references pabel_connector's own internals "
                   "directly - the hook is the only sanctioned way any of this "
                   "runs, never a command the model constructs itself; denied "
                   "unconditionally, never executed.")

    if call.mcp_target and call.mcp_target[0] == PABEL_CONNECTOR_MCP_SERVER_NAME:
        # mcp_local_server.py's own whoami/read_document/login - always
        # sanctioned, and needs no injected agent_token (unlike the branch
        # below): it resolves this installation's identity internally,
        # from how it was registered - see detection.py's constant.
        return Decision(DecisionKind.ALLOW)

    if call.mcp_target and call.mcp_target[0] == PABEL_MCP_SERVER_NAME:
        # A direct model call to pabel's own tools (whoami/read_document) is
        # always sanctioned - but server/core.py's resolve_agent() now
        # requires an agent_token argument the model can never legitimately
        # hold itself. Inject this installation's own credential before
        # allowing the call through, so the model never sees the secret and
        # never needs to. An adapter that can't rewrite input (see
        # Decision.updated_input's docstring) just allows the call
        # unmodified - the server then rejects the missing/invalid
        # agent_token with a clean error, a safe fallback, not a hole.
        try:
            token = agent_session.access_token(agent_id)
        except AuthError:
            return Decision(DecisionKind.ALLOW)  # let the server's own error explain it
        return Decision(DecisionKind.ALLOW,
                        updated_input={**call.tool_input, "agent_token": token})

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
        # See pabel_client/relay.py's read_document_with_login docstring:
        # this runs the interactive browser+MFA login itself, blocking,
        # if there's no valid human session yet, then retries once - the
        # hook is the only place in this whole flow synchronous enough to
        # just wait for one (it's already a blocking subprocess the
        # calling agent is sitting idle on). See installers/base.py's
        # HOOK_TIMEOUT_SECONDS for why every installer now writes a
        # generous hook timeout to give that wait room to complete.
        result = read_document_with_login(str(target), target.name, agent_id)
    except AuthError as e:
        return Decision(DecisionKind.DENY_AUTH_ERROR,
                         reason=f"Not authenticated to the PABEL server: {e}")
    except RelayError as e:
        return Decision(DecisionKind.DENY_RELAY_ERROR,
                         reason=f"Relay to the PABEL server failed: {e}")

    return Decision(
        DecisionKind.DENY_WITH_RELAY,
        reason=f"Direct reads of .abe files are blocked - the PABEL server's "
               f"read_document result for {target.name!r} is provided instead.",
        content=result)
