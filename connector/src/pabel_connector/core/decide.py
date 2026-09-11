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

Since agent_id is never trusted as an identity, the one thing that DOES
have to be protected is whatever ties "this hook fired" to "which agent_id
to use" in the first place - the agent_id argument baked into this
agent's own hook config file at install time (see hook.py's dispatch and
installers/*.py). DENY_CONFIG_TAMPER (detection.is_pabel_hook_config_target)
denies any write to that file, for exactly the same reason
DENY_CREDENTIAL_ACCESS denies touching the credential store itself:
without it, a model could rewrite its own hook to authenticate as a
different, already-installed agent product, or repoint the deployed
server's own URL - achieving the same result as stealing a credential
without ever having to read one.

Every DENY_* produced here that never itself talks to the server
(DENY_CREDENTIAL_ACCESS, DENY_HOOK_BYPASS, DENY_CONFIG_TAMPER,
DENY_MUTATING, DENY_OABE_BINARY, DENY_AMBIGUOUS - via _deny_locally() below)
is also best-effort reported to it (pabel_client/relay.report_denial), so a
tampering attempt still leaves a record in the same audit trail as every
other operation, even though nothing about the tampering attempt itself
required a network call to be denied.

Separately, every PABEL-relevant outcome here (every DENY_* plus the two
"aware" ALLOWs - a direct call to pabel's/mcp_local_server.py's own tools -
never the generic "this call has nothing to do with PABEL" fallback ALLOWs)
is also best-effort written to pabel_client/audit_log.py's local,
confidentiality-only encrypted log (_log_locally() below) - a deliberately
different, complementary record from relay.report_denial's: this one exists
so an employer can cross-reference "what this endpoint's own timeline says
happened" against "what the server's audit trail says happened", including
for the relay path (DENY_WITH_RELAY/DENY_AUTH_ERROR/DENY_RELAY_ERROR),
which report_denial() deliberately skips (already server-audited via the
real read_document attempt) but which _log_locally() does not skip - the
two logs answer different questions, not the same one twice. See
audit_log.py's own docstring for exactly what this guarantees
(confidentiality against a third party who later finds the file) and does
not (integrity against the very agent it records - structurally impossible
for a local, agent-writable file, encrypted or not).
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
    """Build a deny Decision for one of the "never reaches the server on its
    own" kinds: best-effort report it to the deployed server's audit trail
    (relay.report_denial) so it isn't invisible everywhere, and best-effort
    record it in the local encrypted log too (_log_locally) - see this
    module's own docstring for why both exist and answer different
    questions. Neither call can fail loudly or change what's returned below.
    Not used for DENY_WITH_RELAY/DENY_AUTH_ERROR/DENY_RELAY_ERROR, which log
    locally too but skip relay.report_denial specifically (already
    server-audited via the real read_document attempt)."""
    relay.report_denial(agent_id, kind.name, reason, tool_name)
    _log_locally(kind, reason, agent_id, tool_name, target=target)
    return Decision(kind, reason=reason)


def decide(call: NormalizedCall, agent_id: str) -> Decision:
    # Checked before anything else, regardless of read/write/execute: this
    # installation's own local secrets (session.py/agent_session.py) are
    # never a legitimate target for the model itself, and none of the
    # checks below would otherwise catch a plain read of them.
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
        # mcp_local_server.py's own whoami/read_document/login - always
        # sanctioned, and needs no injected agent_token (unlike the branch
        # below): it resolves this installation's identity internally,
        # from how it was registered - see detection.py's constant.
        _log_locally(DecisionKind.ALLOW, "Direct call to this installation's own "
                     "whoami/read_document/login tools.", agent_id, call.tool_name,
                     target=call.mcp_target[1])
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
        # Checked against write_target specifically, never against the
        # whole tool_input: a write's *content* legitimately mentioning a
        # protected path (writing documentation, for instance) must not be
        # confused with writing *to* one.
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
