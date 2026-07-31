"""Thin wrapper around the OpenABE command-line tools (oabe_*).

Every operation shells out to the CLI: oabe_setup / oabe_keygen for the
authority and per-(user, agent) keys, oabe_enc / oabe_dec for document
sections. Key material and ciphertext are always handled as bytes at the
call site (server/core.py) - this module only ever touches the filesystem
for the CLI's own temp input/output files, which it deletes immediately
after reading, since Postgres (not the filesystem) is the persistent store
for keys.

The executables are looked up in PATH first, then via OPENABE_BIN_DIR (see
.env.example) - there is no relative-path guess between this project and
wherever OpenABE happens to be checked out, since that relationship isn't
fixed.
"""

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import env

ROOT = Path(__file__).resolve().parent
AUTHORITY_DIR = ROOT / "authority"
AUTHORITY = "org"

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Prefixes used by keygen()/decrypt_bytes()/encrypt_bytes() below for their
# short-lived temp files - each already deletes its own in a `finally`
# block, success or failure. This list is only a crash-recovery net (e.g.
# the process gets killed mid-request) - see cleanup_stale_temp_files().
TEMP_FILE_PREFIXES = ("abe_key_", "abe_ct_", "abe_pt_", "abe_enc_")


def cleanup_stale_temp_files(max_age_seconds=300):
    """Delete any of this module's temp files left behind by a killed
    process (normal completion already cleans up synchronously - this is
    only for the crash case). Meant to run once at server startup, not
    per-request."""
    now = time.time()
    tmp_dir = Path(tempfile.gettempdir())
    for prefix in TEMP_FILE_PREFIXES:
        for path in tmp_dir.glob(f"{prefix}*"):
            try:
                if now - path.stat().st_mtime > max_age_seconds:
                    path.unlink()
            except OSError:
                pass  # deleted by another process, or a permissions quirk - not fatal


def find_bin():
    """Return the oabe_* executables folder, "" if already in PATH."""
    if shutil.which("oabe_dec.exe") or shutil.which("oabe_dec"):
        return ""
    env.load()
    configured = os.environ.get("OPENABE_BIN_DIR")
    if configured and (Path(configured) / "oabe_setup.exe").exists():
        return configured
    raise FileNotFoundError(
        "OpenABE executables not found on PATH. Set OPENABE_BIN_DIR in "
        ".env to the folder containing oabe_setup.exe/oabe_keygen.exe/"
        "oabe_enc.exe/oabe_dec.exe (and their DLLs).")


def _run(command, args):
    bin_dir = find_bin()
    run_env = os.environ.copy()
    if bin_dir:
        run_env["PATH"] = bin_dir + os.pathsep + run_env["PATH"]
    subprocess.run(
        [command] + args,
        cwd=AUTHORITY_DIR, env=run_env,
        capture_output=True,
        creationflags=CREATE_NO_WINDOW,
    )


def authority_exists():
    return (AUTHORITY_DIR / f"{AUTHORITY}.mpk.cpabe").exists()


def setup_authority():
    """Create the authority key pair (org.mpk.cpabe / org.msk.cpabe)."""
    AUTHORITY_DIR.mkdir(exist_ok=True)
    _run("oabe_setup", ["-s", "CP", "-p", AUTHORITY])
    if not authority_exists():
        raise RuntimeError("oabe_setup failed: no org.mpk.cpabe produced")


def keygen(attributes):
    """Generate a key for the given '|'-joined attribute string and return
    its raw bytes. Never leaves a key file behind - Postgres is the only
    persistent store for key material."""
    fd, tmp = tempfile.mkstemp(suffix="", prefix="abe_key_")
    os.close(fd)
    os.remove(tmp)  # oabe_keygen creates <tmp>.key itself
    tmp_key = Path(f"{tmp}.key")
    try:
        _run("oabe_keygen", ["-s", "CP", "-p", AUTHORITY,
                             "-i", attributes, "-o", tmp])
        if not tmp_key.exists():
            raise RuntimeError(f"oabe_keygen failed for attributes: {attributes!r}")
        return tmp_key.read_bytes()
    finally:
        if tmp_key.exists():
            os.remove(tmp_key)


def decrypt_bytes(key_bytes, ciphertext):
    """Return the plaintext, or None when the key does not satisfy the policy."""
    kfd, key_path = tempfile.mkstemp(suffix=".key", prefix="abe_key_")
    cfd, ct_path = tempfile.mkstemp(suffix=".cpabe", prefix="abe_ct_")
    out_fd, out_path = tempfile.mkstemp(suffix=".txt", prefix="abe_pt_")
    os.close(out_fd)
    os.remove(out_path)  # oabe_dec creates the output file only on success
    try:
        with os.fdopen(kfd, "wb") as f:
            f.write(key_bytes)
        with os.fdopen(cfd, "wb") as f:
            f.write(ciphertext)
        _run("oabe_dec", ["-s", "CP", "-p", AUTHORITY,
                          "-k", key_path, "-i", ct_path, "-o", out_path])
        if os.path.exists(out_path):
            # utf-8-sig: tolerate a BOM in the original plaintext
            return Path(out_path).read_text(encoding="utf-8-sig")
        return None
    finally:
        for p in (key_path, ct_path, out_path):
            if os.path.exists(p):
                os.remove(p)


def encrypt_bytes(text, policy):
    """Encrypt text under an ABE policy and return the raw ciphertext bytes."""
    in_fd, in_path = tempfile.mkstemp(suffix=".txt", prefix="abe_enc_")
    out_fd, out_path = tempfile.mkstemp(suffix=".cpabe", prefix="abe_ct_")
    os.close(out_fd)
    os.remove(out_path)
    try:
        with os.fdopen(in_fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        _run("oabe_enc", ["-s", "CP", "-p", AUTHORITY, "-e", policy,
                          "-i", in_path, "-o", out_path])
        if not os.path.exists(out_path):
            raise RuntimeError(f"encryption failed for policy: {policy!r}")
        return Path(out_path).read_bytes()
    finally:
        if os.path.exists(in_path):
            os.remove(in_path)
        if os.path.exists(out_path):
            os.remove(out_path)
