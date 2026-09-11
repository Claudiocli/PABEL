import json

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from pabel_connector.pabel_client import audit_log

_OAEP_PADDING = padding.OAEP(
    mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None)


@pytest.fixture
def keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


def _decrypt(private_key, line):
    # Independent reimplementation of the same format, deliberately not
    # importing server/agents_admin.py's own decrypt (separate packages,
    # never cross-import - see connector/tests' existing conventions) -
    # this proves the wire format itself is right, not just that
    # encrypt_entry agrees with its own inverse.
    import base64
    raw = base64.b64decode(line)
    key_len = int.from_bytes(raw[:2], "big")
    wrapped_key = raw[2:2 + key_len]
    nonce = raw[2 + key_len:2 + key_len + 12]
    ciphertext = raw[2 + key_len + 12:]
    aes_key = private_key.decrypt(wrapped_key, _OAEP_PADDING)
    return json.loads(AESGCM(aes_key).decrypt(nonce, ciphertext, None))


def test_encrypt_entry_round_trips(keypair):
    private_key, public_key = keypair
    entry = {"ts": 123.0, "agent_id": "claude-code", "tool_name": "Write",
             "decision_kind": "DENY_CONFIG_TAMPER", "target": ".claude/settings.json",
             "reason": "some reason"}
    line = audit_log.encrypt_entry(public_key, entry)
    assert _decrypt(private_key, line) == entry


def test_encrypt_entry_produces_different_ciphertext_each_time(keypair):
    # A fresh AES key + nonce per entry - two identical entries must not be
    # bit-for-bit identical on the wire (that alone would leak that they're
    # the same, defeating part of the point of encrypting at all).
    _, public_key = keypair
    entry = {"ts": 1, "agent_id": "x", "tool_name": "y", "decision_kind": "z",
             "target": None, "reason": "r"}
    assert audit_log.encrypt_entry(public_key, entry) != audit_log.encrypt_entry(public_key, entry)


def test_install_public_key_rejects_a_private_key(tmp_path, monkeypatch):
    monkeypatch.setattr(audit_log, "DATA_DIR", tmp_path / "pabel_home")
    monkeypatch.setattr(audit_log, "PUBLIC_KEY_FILE", tmp_path / "pabel_home" / "audit_public_key.pem")
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    bad_path = tmp_path / "not_actually_public.pem"
    bad_path.write_bytes(private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()))
    with pytest.raises(Exception):
        audit_log.install_public_key(bad_path)
    assert not audit_log.PUBLIC_KEY_FILE.exists()  # fail fast, before writing anything


def test_install_public_key_then_load_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(audit_log, "DATA_DIR", tmp_path / "pabel_home")
    monkeypatch.setattr(audit_log, "PUBLIC_KEY_FILE", tmp_path / "pabel_home" / "audit_public_key.pem")
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    source = tmp_path / "audit_public_key.pem"
    source.write_bytes(private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo))
    audit_log.install_public_key(source)
    loaded = audit_log.load_public_key()
    assert loaded is not None
    assert loaded.public_numbers() == private_key.public_key().public_numbers()


def test_load_public_key_returns_none_when_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(audit_log, "DATA_DIR", tmp_path / "pabel_home")
    monkeypatch.setattr(audit_log, "PUBLIC_KEY_FILE", tmp_path / "pabel_home" / "audit_public_key.pem")
    assert audit_log.load_public_key() is None


def test_append_entry_is_a_silent_no_op_without_a_public_key(tmp_path, monkeypatch):
    monkeypatch.setattr(audit_log, "DATA_DIR", tmp_path / "pabel_home")
    monkeypatch.setattr(audit_log, "PUBLIC_KEY_FILE", tmp_path / "pabel_home" / "audit_public_key.pem")
    monkeypatch.setattr(audit_log, "LOG_FILE", tmp_path / "pabel_home" / "audit.log.enc")
    audit_log.append_entry("claude-code", "Write", "DENY_MUTATING", "test.abe", "reason")  # must not raise
    assert not audit_log.LOG_FILE.exists()


def test_append_entry_writes_a_decryptable_line(tmp_path, monkeypatch, keypair):
    private_key, public_key = keypair
    monkeypatch.setattr(audit_log, "DATA_DIR", tmp_path / "pabel_home")
    key_file = tmp_path / "pabel_home" / "audit_public_key.pem"
    monkeypatch.setattr(audit_log, "PUBLIC_KEY_FILE", key_file)
    monkeypatch.setattr(audit_log, "LOG_FILE", tmp_path / "pabel_home" / "audit.log.enc")
    key_file.parent.mkdir(parents=True)
    key_file.write_bytes(public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo))

    audit_log.append_entry("claude-code", "Write", "DENY_CONFIG_TAMPER",
                           ".claude/settings.json", "some reason")
    lines = audit_log.LOG_FILE.read_text(encoding="ascii").splitlines()
    assert len(lines) == 1
    entry = _decrypt(private_key, lines[0])
    assert entry["agent_id"] == "claude-code"
    assert entry["decision_kind"] == "DENY_CONFIG_TAMPER"
    assert entry["target"] == ".claude/settings.json"


def test_append_entry_never_raises_on_a_corrupt_key_file(tmp_path, monkeypatch):
    monkeypatch.setattr(audit_log, "DATA_DIR", tmp_path / "pabel_home")
    key_file = tmp_path / "pabel_home" / "audit_public_key.pem"
    monkeypatch.setattr(audit_log, "PUBLIC_KEY_FILE", key_file)
    monkeypatch.setattr(audit_log, "LOG_FILE", tmp_path / "pabel_home" / "audit.log.enc")
    key_file.parent.mkdir(parents=True)
    key_file.write_text("not a real pem file")
    audit_log.append_entry("claude-code", "Write", "DENY_MUTATING", "test.abe", "reason")  # must not raise
