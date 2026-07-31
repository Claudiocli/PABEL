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
    """Sections sharing one policy, encrypted together in one ciphertext."""

    def __init__(self, policy, ciphertext):
        self.policy = policy
        self.ciphertext = ciphertext  # raw bytes
        self.sections = []

    def decrypt_with(self, key_bytes):
        """The section texts in order, or None when the key is not entitled."""
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
        groups = [Group(g.get("policy"), base64.b64decode(g["ciphertext"]))
                  for g in doc["groups"]]
        sections = [Section(f"section {i}", groups[entry["group"]])
                    for i, entry in enumerate(doc["sections"], start=1)]
        return sections, groups
    except (KeyError, TypeError, AttributeError, IndexError) as e:
        raise ValueError(f"malformed .abe document: {e!r}") from e
