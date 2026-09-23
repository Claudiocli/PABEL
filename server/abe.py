"""Thin wrapper around the OpenABE command-line tools (oabe_*).

Every operation shells out to the CLI: oabe_setup / oabe_keygen for the
authority and per-(user, agent) keys, oabe_dec to decrypt document
sections. Key material and ciphertext are always handled as bytes at the
call site (server/core.py) - this module only ever touches the filesystem
for the CLI's own temp input/output files, which it deletes immediately
after reading, since Postgres (not the filesystem) is the persistent store
for keys.

Deliberately decrypt-only: encrypting/authoring a new .abe document (what
oabe_enc would wrap) is explicitly out of scope for this project - see
docs/known-gaps.md's "Encrypting/authoring .abe documents" section for why
this is the company's own responsibility, not a gap in this codebase.

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

# Prefixes used by keygen()/decrypt_bytes() below for their short-lived temp
# files - each already deletes its own in a `finally` block, success or
# failure. This list is only a crash-recovery net (e.g. the process gets
# killed mid-request) - see cleanup_stale_temp_files().
TEMP_FILE_PREFIXES = ("abe_key_", "abe_ct_", "abe_pt_")


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
    """Run an oabe_* tool and return the CompletedProcess.

    The output is returned rather than discarded so a failure can say why.
    These tools report most errors on stdout/stderr while still exiting 0,
    so the callers below judge success by whether the expected file
    appeared - but when it didn't, the reason is the only thing worth
    having."""
    bin_dir = find_bin()
    run_env = os.environ.copy()
    if bin_dir:
        run_env["PATH"] = bin_dir + os.pathsep + run_env["PATH"]
    return subprocess.run(
        [command] + args,
        cwd=AUTHORITY_DIR, env=run_env,
        capture_output=True, text=True, errors="replace",
        creationflags=CREATE_NO_WINDOW,
    )


def _diagnostics(proc):
    """The tool's own output, flattened onto one line for an exception."""
    parts = [(proc.stdout or "").strip(), (proc.stderr or "").strip()]
    detail = " / ".join(p for p in parts if p) or "(no output)"
    return f"exit={proc.returncode}: {detail}"


def authority_exists():
    return (AUTHORITY_DIR / f"{AUTHORITY}.mpk.cpabe").exists()


def setup_authority():
    """Create the authority key pair (org.mpk.cpabe / org.msk.cpabe)."""
    AUTHORITY_DIR.mkdir(exist_ok=True)
    proc = _run("oabe_setup", ["-s", "CP", "-p", AUTHORITY])
    if not authority_exists():
        raise RuntimeError(
            f"oabe_setup failed: no {AUTHORITY}.mpk.cpabe produced - {_diagnostics(proc)}")


def keygen(attributes):
    """Generate a key for the given '|'-joined attribute string and return
    its raw bytes. Never leaves a key file behind - Postgres is the only
    persistent store for key material."""
    fd, tmp = tempfile.mkstemp(suffix="", prefix="abe_key_")
    os.close(fd)
    os.remove(tmp)  # oabe_keygen creates <tmp>.key itself
    tmp_key = Path(f"{tmp}.key")
    try:
        proc = _run("oabe_keygen", ["-s", "CP", "-p", AUTHORITY,
                                    "-i", attributes, "-o", tmp])
        if not tmp_key.exists():
            raise RuntimeError(
                f"oabe_keygen failed for attributes: {attributes!r} - "
                f"{_diagnostics(proc)} (authority dir: {AUTHORITY_DIR}, "
                f"master key present: {(AUTHORITY_DIR / f'{AUTHORITY}.msk.cpabe').exists()})")
        return tmp_key.read_bytes()
    finally:
        if tmp_key.exists():
            os.remove(tmp_key)


def _lf(data):
    """Normalize CRLF to LF in an OpenABE artifact.

    Every artifact the CLI produces - authority, keys, ciphertext - is
    PEM-shaped text (a header line, one base64 line, a footer line) written
    through cli/common.cpp's WriteToFile(), which opens the file in *text*
    mode. On Windows that turns each \\n into \\r\\n; on Linux it does not.
    The readers are not symmetric about this: ReadFile() finds its payload
    with find() and hands it to a base64 decoder that ignores a stray \\r,
    but ReadBlockFromFile() - the one decrypt.cpp uses for the ciphertext -
    matches its header with an exact compare(). A Windows-written ciphertext
    read by a Linux build therefore never matches "-----BEGIN ...-----"
    against "-----BEGIN ...-----\\r", leaves the block empty, and decodes
    nothing.

    That is why a document authored on Windows decrypts on a Windows host
    and fails in a Linux container, while keys and the authority cross
    platforms unharmed - it was never a curve, word-size or build mismatch.
    Normalizing here costs nothing on Windows (whose reader strips \\r
    anyway) and makes the artifact readable by either build.
    """
    return data.replace(b"\r\n", b"\n")


def decrypt_bytes(key_bytes, ciphertext):
    """Return the plaintext, or None when the key does not satisfy the policy."""
    kfd, key_path = tempfile.mkstemp(suffix=".key", prefix="abe_key_")
    cfd, ct_path = tempfile.mkstemp(suffix=".cpabe", prefix="abe_ct_")
    out_fd, out_path = tempfile.mkstemp(suffix=".txt", prefix="abe_pt_")
    os.close(out_fd)
    os.remove(out_path)  # oabe_dec creates the output file only on success
    try:
        # The key goes through _lf() too: a key minted by a Windows build
        # and cached in Postgres is read back by whichever build serves the
        # next request, which need not be the same one.
        with os.fdopen(kfd, "wb") as f:
            f.write(_lf(key_bytes))
        with os.fdopen(cfd, "wb") as f:
            f.write(_lf(ciphertext))
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
