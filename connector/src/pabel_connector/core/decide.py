"""The one agent-agnostic policy: given a NormalizedCall, decide whether it
touches a PABEL-protected .abe file and what should happen.

Never sees which agent produced the call, beyond `agent_id` - and that is
purely a local key selecting which stored credential to use, never a claimed
identity sent anywhere. What the server actually trusts is a verified
Keycloak token; see server/core.py's resolve_agent().

Since agent_id isn't an identity, what must be protected is whatever ties
"this hook fired" to "which agent_id to use" - the value baked into the
agent's own config file at install time. DENY_CONFIG_TAMPER denies writes to
that file for the same reason DENY_CREDENTIAL_ACCESS denies touching the
credential store: rewriting it would let a model authenticate as a different
installed product, or repoint the server URL, without ever reading a secret.

**Two complementary audit trails, answering different questions.**
`relay.report_denial` records denials that never otherwise reach the server,
so a tampering attempt still lands in the server's own trail. `_log_locally`
writes every PABEL-relevant outcome - including the relay paths, which
report_denial skips as already server-audited - to a local encrypted log, so
an employer can cross-reference the endpoint's timeline against the server's.
Both are best-effort and neither can change what's returned. See
audit_log.py for what that local log does (confidentiality) and does not
(integrity against the agent it records) guarantee.
"""

from ..pabel_client import agent_session, audit_log, relay
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
    is_pabel_hook_config_target,
    mentions_target,
    touches_pabel_credential_store,
)
from .types import Decision, DecisionKind, NormalizedCall


def _log_locally(kind: DecisionKind, reason: str, agent_id: str, tool_name: str,
                 target: str = None) -> None:
    audit_log.append_entry(agent_id, tool_name, kind.name, target, reason)


def _deny_locally(kind: DecisionKind, reason: str, agent_id: str, tool_name: str,
                  target: str = None) -> Decision:
    """Deny for one of the kinds that never reaches the server on its own, so
    it gets reported there as well as logged locally. Not used for the relay
    outcomes, which log locally but skip report_denial as already audited."""
    relay.report_denial(agent_id, kind.name, reason, tool_name)
    _log_locally(kind, reason, agent_id, tool_name, target=target)
    return Decision(kind, reason=reason)


def decide(call: NormalizedCall, agent_id: str) -> Decision:
    # Checked before anything else, regardless of read/write/execute: this
    # installation's own secrets are never a legitimate target, and no check
    # below would otherwise catch a plain read of them.
    if touches_pabel_credential_store(call.tool_input):
        return _deny_locally(
            DecisionKind.DENY_CREDENTIAL_ACCESS,
            "This targets PABEL's own local credential store (this "
            "installation's human session or agent client secret/token) - "
            "only this package's own code ever reads these; denied "
            "unconditionally, never relayed. To check who is currently "
            "logged in (or why a document section came back access-denied) "
            "without reading any local file, call the pabel MCP server's own "
            "whoami tool instead - it reports username, ABE attributes, and "
            "authorization status directly, and is always a sanctioned call.",
            agent_id, call.tool_name)

    if call.is_execute and invokes_pabel_connector_internals(call.tool_input):
        return _deny_locally(
            DecisionKind.DENY_HOOK_BYPASS,
            "This command references pabel_connector's own internals "
            "directly - the hook is the only sanctioned way any of this "
            "runs, never a command the model constructs itself; denied "
            "unconditionally, never executed.",
            agent_id, call.tool_name)

    if call.mcp_target and call.mcp_target[0] == PABEL_CONNECTOR_MCP_SERVER_NAME:
        # Needs no injected agent_token, unlike the branch below: this server
        # resolves the installation's identity internally, from how it was
        # registered.
        _log_locally(DecisionKind.ALLOW, "Direct call to this installation's own "
                     "whoami/read_document/login tools.", agent_id, call.tool_name,
                     target=call.mcp_target[1])
        return Decision(DecisionKind.ALLOW)

    if call.mcp_target and call.mcp_target[0] == PABEL_MCP_SERVER_NAME:
        # resolve_agent() requires an agent_token the model can never
        # legitimately hold, so inject this installation's own. An adapter
        # that can't rewrite input allows the call unmodified and the server
        # rejects it cleanly - a safe fallback, not a hole.
        try:
            token = agent_session.access_token(agent_id)
        except AuthError:
            _log_locally(DecisionKind.ALLOW, "Direct call to the deployed pabel "
                         "server's own tools, but no stored credential for this "
                         "installation - allowed through unmodified, the server's "
                         "own error will explain it.", agent_id, call.tool_name,
                         target=call.mcp_target[1])
            return Decision(DecisionKind.ALLOW)  # let the server's own error explain it
        _log_locally(DecisionKind.ALLOW, "Direct call to the deployed pabel server's "
                     "own tools, with this installation's own credential injected.",
                     agent_id, call.tool_name, target=call.mcp_target[1])
        return Decision(DecisionKind.ALLOW,
                        updated_input={**call.tool_input, "agent_token": token})

    if call.is_write:
        # write_target specifically, never the whole tool_input: a write whose
        # *content* mentions a protected path must not be confused with a
        # write *to* one.
        target_path = call.write_target or ""
        if ENCRYPTED_FILE.search(target_path) or DOCUMENTS_PATH.search(target_path):
            return _deny_locally(
                DecisionKind.DENY_MUTATING,
                "This project has no write/authoring path for .abe files - "
                "read_document on the deployed PABEL server is the only "
                "sanctioned operation.",
                agent_id, call.tool_name, target=target_path)
        if is_pabel_hook_config_target(target_path):
            return _deny_locally(
                DecisionKind.DENY_CONFIG_TAMPER,
                "This targets this installation's own hook/MCP registration file - "
                "only `pabel-connector install`/`uninstall`, run directly by a "
                "human, are sanctioned to change it. The agent_id baked into it at "
                "install time is what ties this hook to which stored credential it "
                "uses (see core/decide.py's own docstring) - rewriting it here "
                "could let this installation start authenticating as a different "
                "agent product, or repoint the deployed server's own URL "
                "entirely. Denied unconditionally, never relayed.",
                agent_id, call.tool_name, target=target_path)
        return Decision(DecisionKind.ALLOW)

    if call.is_execute and invokes_oabe_binary(call.tool_input):
        return _deny_locally(
            DecisionKind.DENY_OABE_BINARY,
            "This invokes an OpenABE CLI binary directly. Decryption must "
            "happen on the deployed PABEL server, which combines the "
            "current user's and agent's attributes into one key and "
            "audits the result - calling oabe_dec/oabe_keygen/oabe_setup "
            "directly would bypass both.",
            agent_id, call.tool_name)

    if not mentions_target(call.tool_input):
        return Decision(DecisionKind.ALLOW)

    target = find_relayable_file(call.tool_input)
    if target is None:
        return _deny_locally(
            DecisionKind.DENY_AMBIGUOUS,
            "This targets an .abe file or the documents/ folder in a way "
            "that doesn't name one concrete existing file (a directory, a "
            "pattern, or several candidates) - relaying automatically "
            "isn't possible here. Ask for one specific file by name.",
            agent_id, call.tool_name)

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
        reason = f"Not authenticated to the PABEL server: {e}"
        _log_locally(DecisionKind.DENY_AUTH_ERROR, reason, agent_id, call.tool_name,
                     target=str(target))
        return Decision(DecisionKind.DENY_AUTH_ERROR, reason=reason)
    except RelayError as e:
        reason = f"Relay to the PABEL server failed: {e}"
        _log_locally(DecisionKind.DENY_RELAY_ERROR, reason, agent_id, call.tool_name,
                     target=str(target))
        return Decision(DecisionKind.DENY_RELAY_ERROR, reason=reason)

    reason = (f"Direct reads of .abe files are blocked - the PABEL server's "
             f"read_document result for {target.name!r} is provided instead.")
    _log_locally(DecisionKind.DENY_WITH_RELAY, reason, agent_id, call.tool_name,
                 target=str(target))
    return Decision(DecisionKind.DENY_WITH_RELAY, reason=reason, content=result)
