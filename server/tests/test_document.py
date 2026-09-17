"""document.load_abe / Group: gated and ungated groups side by side.

Found live on 2026-09-17: a protected PDF written by ABEditor stores its
redacted layout as an ungated "plaintext" group, and load_abe indexed
g["ciphertext"] unconditionally - so a perfectly valid document came back
as `malformed .abe document: KeyError('ciphertext')`. The reader
implemented a narrower format than the writer produced.
"""

import base64
import json

import pytest

import document


def encode(text):
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def build(groups, sections):
    return json.dumps({"format": "abe-doc", "version": 2,
                       "groups": groups, "sections": sections})


def test_ungated_group_is_readable_without_any_key():
    content = build([{"plaintext": "the redacted layout"}], [{"group": 0}])
    sections, groups = document.load_abe(content)
    assert groups[0].decrypt_with(b"irrelevant") == ["the redacted layout"]
    assert sections[0].policy is None


def test_gated_group_still_goes_through_abe(monkeypatch):
    monkeypatch.setattr(document.abe, "decrypt_bytes",
                        lambda key, ct: "secret" if key == b"good" else None)
    content = build([{"policy": "livello >= 2", "ciphertext": encode("x")}],
                    [{"group": 0}])
    _sections, groups = document.load_abe(content)
    assert groups[0].decrypt_with(b"good") == ["secret"]
    assert groups[0].decrypt_with(b"bad") is None


def test_one_document_mixes_both(monkeypatch):
    """The shape a protected PDF actually has: an ungated layout plus
    policy-gated regions. Neither kind may disturb the other."""
    monkeypatch.setattr(document.abe, "decrypt_bytes", lambda key, ct: None)
    content = build(
        [{"plaintext": "layout"}, {"policy": "ruolo_dev", "ciphertext": encode("region")}],
        [{"group": 0}, {"group": 1}])
    sections, groups = document.load_abe(content)
    assert groups[0].decrypt_with(b"") == ["layout"]
    assert groups[1].decrypt_with(b"") is None
    assert len(sections) == 2


def test_group_with_neither_payload_is_rejected():
    """Not silently empty: a group with no payload would otherwise be
    indistinguishable from a policy denial."""
    with pytest.raises(ValueError, match="neither"):
        document.load_abe(build([{"policy": "x"}], [{"group": 0}]))


def test_multi_section_group_payload_is_a_json_array():
    content = build([{"plaintext": json.dumps(["first", "second"])}],
                    [{"group": 0}, {"group": 0}])
    _sections, groups = document.load_abe(content)
    assert groups[0].decrypt_with(b"") == ["first", "second"]
