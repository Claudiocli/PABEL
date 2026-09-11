import base64
import json

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import agents_admin

_OAEP_PADDING = padding.OAEP(
    mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None)


def _encrypt_like_the_connector_does(public_key, entry):
    # Deliberately not importing connector/pabel_client/audit_log.py -
    # separate packages, never cross-import (same convention as every
    # other server test). Proves the two independent implementations of
    # the same wire format actually agree, rather than one test module
    # trivially agreeing with itself.
    plaintext = json.dumps(entry).encode("utf-8")
    aes_key = AESGCM.generate_key(bit_length=256)
    nonce = b"0" * 12
    ciphertext = AESGCM(aes_key).encrypt(nonce, plaintext, None)
    wrapped_key = public_key.encrypt(aes_key, _OAEP_PADDING)
    return base64.b64encode(len(wrapped_key).to_bytes(2, "big") + wrapped_key
                            + nonce + ciphertext).decode("ascii")


def test_generate_audit_keypair_writes_a_usable_pair(tmp_path):
    agents_admin.do_generate_audit_keypair(str(tmp_path))
    private_path = tmp_path / "audit_private_key.pem"
    public_path = tmp_path / "audit_public_key.pem"
    assert private_path.exists()
    assert public_path.exists()
    private_key = serialization.load_pem_private_key(private_path.read_bytes(), password=None)
    public_key = serialization.load_pem_public_key(public_path.read_bytes())
    assert private_key.public_key().public_numbers() == public_key.public_numbers()


def test_generate_audit_keypair_private_key_is_unencrypted_pkcs8(tmp_path):
    # Deliberately no passphrase on the private key file itself - this
    # project relies on wherever IT stores it (a vault, an HSM, its own
    # access controls), not a passphrase baked into the generation step.
    agents_admin.do_generate_audit_keypair(str(tmp_path))
    text = (tmp_path / "audit_private_key.pem").read_text()
    assert "BEGIN PRIVATE KEY" in text
    assert "ENCRYPTED" not in text


def test_decrypt_audit_log_recovers_real_entries(tmp_path, capsys):
    agents_admin.do_generate_audit_keypair(str(tmp_path))
    capsys.readouterr()  # discard generate-audit-keypair's own "Wrote ..." output
    public_key = serialization.load_pem_public_key(
        (tmp_path / "audit_public_key.pem").read_bytes())
    entry_a = {"ts": 1, "agent_id": "claude-code", "tool_name": "Write",
              "decision_kind": "DENY_CONFIG_TAMPER", "target": ".mcp.json", "reason": "r1"}
    entry_b = {"ts": 2, "agent_id": "cursor:beforeReadFile", "tool_name": "Read",
              "decision_kind": "DENY_WITH_RELAY", "target": "documents/Test.abe", "reason": "r2"}
    log_file = tmp_path / "audit.log.enc"
    log_file.write_text(
        _encrypt_like_the_connector_does(public_key, entry_a) + "\n"
        + _encrypt_like_the_connector_does(public_key, entry_b) + "\n")

    agents_admin.do_decrypt_audit_log(str(log_file), str(tmp_path / "audit_private_key.pem"))
    lines = capsys.readouterr().out.strip().splitlines()
    assert [json.loads(line) for line in lines] == [entry_a, entry_b]


def test_decrypt_audit_log_skips_corrupt_lines_without_stopping(tmp_path, capsys):
    agents_admin.do_generate_audit_keypair(str(tmp_path))
    capsys.readouterr()  # discard generate-audit-keypair's own "Wrote ..." output
    public_key = serialization.load_pem_public_key(
        (tmp_path / "audit_public_key.pem").read_bytes())
    good_entry = {"ts": 1, "agent_id": "x", "tool_name": "y",
                 "decision_kind": "z", "target": None, "reason": "r"}
    log_file = tmp_path / "audit.log.enc"
    log_file.write_text(
        "not valid base64 at all!!\n"
        + _encrypt_like_the_connector_does(public_key, good_entry) + "\n")

    agents_admin.do_decrypt_audit_log(str(log_file), str(tmp_path / "audit_private_key.pem"))
    out, err = capsys.readouterr()
    assert "could not decrypt" in err
    assert json.loads(out.strip()) == good_entry


def test_decrypt_audit_log_ignores_blank_lines(tmp_path, capsys):
    agents_admin.do_generate_audit_keypair(str(tmp_path))
    capsys.readouterr()  # discard generate-audit-keypair's own "Wrote ..." output
    log_file = tmp_path / "audit.log.enc"
    log_file.write_text("\n\n")
    agents_admin.do_decrypt_audit_log(str(log_file), str(tmp_path / "audit_private_key.pem"))
    out, err = capsys.readouterr()
    assert out == ""
    assert err == ""
