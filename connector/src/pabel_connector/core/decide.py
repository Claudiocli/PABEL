"""The one agent-agnostic policy: given a NormalizedCall, decide whether it
touches a PABEL-protected .abe file and, if so, what should happen. Every
adapter's render() turns this same Decision into its own agent's response
shape - this function never sees or cares which agent produced the call,
beyond agent_id itself: purely a local key selecting which of this
installation's own stored credentials to use (agent_session.py) - never a
value sent anywhere as a claimed identity. See server/core.py's
resolve_agent() for what the server actually trusts (a verified Keycloak
token, not this string).

On an otherwise-relayable read with no valid human session, this also
triggers the interactive browser+MFA login itself and retries once - see
the try/except around read_document() below for why a blocking hook
subprocess, not a human running a separate CLI command later, is the
right place for that.
"""

from ..pabel_client import agent_session, session
from ..pabel_client.keycloak_client import AuthError
from ..pabel_client.relay import RelayError, read_document
from .detection import (
    ENCRYPTED_FILE,
    DOCUMENTS_PATH,
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
                   "unconditionally, never relayed.")

    if call.is_execute and invokes_pabel_connector_internals(call.tool_input):
        return Decision(
            DecisionKind.DENY_HOOK_BYPASS,
            reason="This command references pabel_connector's own internals "
                   "directly - the hook is the only sanctioned way any of this "
                   "runs, never a command the model constructs itself; denied "
                   "unconditionally, never executed.")

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
        result = read_document(str(target), target.name, agent_id)
    except AuthError:
        # No valid human session yet - the hook is the only place in this
        # whole flow synchronous enough to just wait for one: it runs
        # as a blocking subprocess the calling agent is already sitting
        # idle on, so triggering the interactive browser+MFA login here
        # (rather than denying and telling a human to run a separate CLI
        # command by hand, which this project spent an entire session
        # discovering is where every real attempt got stuck) turns "ask a
        # human to go run a command" into "the file just doesn't appear
        # until you finish logging in, then it does." See
        # installers/base.py's HOOK_TIMEOUT_SECONDS for why every
        # installer now writes a generous hook timeout - this can block
        # for the length of session.login()'s own browser-callback wait.
        try:
            session.login()
        except AuthError as e:
            return Decision(
                DecisionKind.DENY_AUTH_ERROR,
                reason=f"Not authenticated to the PABEL server, and the automatic "
                       f"browser login could not complete: {e}")
        try:
            result = read_document(str(target), target.name, agent_id)
        except AuthError as e:
            return Decision(DecisionKind.DENY_AUTH_ERROR,
                             reason=f"Logged in, but still not authenticated: {e}")
        except RelayError as e:
            return Decision(DecisionKind.DENY_RELAY_ERROR,
                             reason=f"Relay to the PABEL server failed after login: {e}")
    except RelayError as e:
        return Decision(DecisionKind.DENY_RELAY_ERROR,
                         reason=f"Relay to the PABEL server failed: {e}")

    return Decision(
        DecisionKind.DENY_WITH_RELAY,
        reason=f"Direct reads of .abe files are blocked - the PABEL server's "
               f"read_document result for {target.name!r} is provided instead.",
        content=result)
