"""Document model for .abe files: read/decrypt path only.

An .abe document is a single JSON file. Sections sharing the same policy
are encrypted together in one "group" ciphertext, so opening a document
costs one oabe_dec call per distinct policy, not per section:

    { "format": "abe-doc", "version": 2,
      "sections": [ { "group": 0 }, { "group": 1 }, { "group": 0 } ],
      "groups":   [ { "policy": "livello >= 2 and agent_claude_code",
                      "ciphertext": "<base64>" },
                    ... ] }

A group payload is the plain text itself for a single section, or a JSON
array of the section texts (in document order) when the group has more.

A group carries either "ciphertext" (encrypted under its policy) or
"plaintext" (ungated - content the author chose to leave readable by
everyone). Protected PDFs use the second for the redacted layout the
encrypted regions are drawn onto; see ABEditor's pdfdoc.py, which writes
this format. What a payload *contains* - text, JSON, base64 PDF - is not
this module's concern and never has been: only whether it is gated is.

This project has no authoring/write path (see server/README.md) and no
legacy vault format - this module only ever loads and decrypts. The exact
on-disk shape above is this project's current working format, not a
settled spec; it may change, and callers should only ever go through
load_abe() rather than assuming this shape elsewhere.
"""

import base64
import json

import abe

FORMAT = "abe-doc"


class Group:
    """Sections sharing one policy, carried together in one payload.

    The payload is either encrypted under `policy` (`ciphertext`) or stored
    in the clear (`plaintext`), and a document mixes both freely. An
    ungated group is not a weaker version of a gated one: it is content the
    author decided everyone may see - a protected PDF's redacted layout,
    say, which only exists so the encrypted regions have somewhere to be
    drawn. Encrypting that under a policy nothing can fail would cost an
    oabe_dec call per read to prove something already known.

    What the payload *contains* is deliberately not this class's business:
    text, JSON, base64 PDF fragments all travel identically."""

    def __init__(self, policy, ciphertext=None, plaintext=None):
        self.policy = policy
        self.ciphertext = ciphertext  # raw bytes, or None when ungated
        self.plaintext = plaintext    # str, or None when gated
        self.sections = []

    def decrypt_with(self, key_bytes):
        """The section texts in order, or None when the key is not entitled."""
        if self.plaintext is not None:
            payload = self.plaintext
        else:
            payload = abe.decrypt_bytes(key_bytes, self.ciphertext)
            if payload is None:
                return None
        return json.loads(payload) if len(self.sections) > 1 else [payload]


class Section:
    """One block of the document, returned independently to the caller."""

    def __init__(self, name, group):
        self.name = name
        self.group = group
        group.sections.append(self)
        self.text = None

    @property
    def policy(self):
        return self.group.policy

    @property
    def accessible(self):
        return self.text is not None


def _group(entry):
    """One group, gated or not. A group carrying neither is rejected rather
    than treated as empty: silently returning no text for it would look
    exactly like a policy denial, which is the one thing a reader must never
    confuse."""
    if "ciphertext" in entry:
        return Group(entry.get("policy"), ciphertext=base64.b64decode(entry["ciphertext"]))
    if "plaintext" in entry:
        return Group(entry.get("policy"), plaintext=entry["plaintext"])
    raise ValueError('group has neither "ciphertext" nor "plaintext"')


def load_abe(content):
    """(sections, groups) from the raw text of an .abe document - handed in
    directly by the caller (see mcp_server.py's read_document), not read
    from a server-side path: the server has no filesystem of its own to
    keep in sync with wherever the agent actually found the file.
    ValueError if not a recognized .abe document."""
    try:
        doc = json.loads(content.lstrip("﻿"))  # tolerate a BOM
        if doc.get("format") != FORMAT:
            raise ValueError('missing "format": "abe-doc"')
        groups = [_group(g) for g in doc["groups"]]
        sections = [Section(f"section {i}", groups[entry["group"]])
                    for i, entry in enumerate(doc["sections"], start=1)]
        return sections, groups
    except (KeyError, TypeError, AttributeError, IndexError) as e:
        raise ValueError(f"malformed .abe document: {e!r}") from e
