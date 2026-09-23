"""abe._lf: an artifact written by a Windows build stays readable by a
Linux one.

Root-caused 2026-09-23, after months of treating Windows/Linux OpenABE as
producing "incompatible key material". It never did. Every oabe_* artifact
is PEM-shaped text written through cli/common.cpp's WriteToFile(), which
opens in text mode - so a Windows build emits CRLF. The readers disagree
about that: ReadFile() locates its payload with find() and the base64
decoder ignores a stray \\r, but ReadBlockFromFile() - what decrypt.cpp
uses for the *ciphertext* - matches its header with an exact compare().
Hence the asymmetry that made this look like a build mismatch: authority
and keys crossed platforms fine, ciphertext did not.
"""

import abe

CRLF_CIPHERTEXT = (b"-----BEGIN CIPHERTEXT-----\r\n"
                   b"QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo=\r\n"
                   b"-----END CIPHERTEXT-----\r\n")

LF_CIPHERTEXT = (b"-----BEGIN CIPHERTEXT-----\n"
                 b"QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo=\n"
                 b"-----END CIPHERTEXT-----\n")


def test_crlf_artifact_becomes_lf():
    assert abe._lf(CRLF_CIPHERTEXT) == LF_CIPHERTEXT


def test_lf_artifact_is_left_alone():
    assert abe._lf(LF_CIPHERTEXT) == LF_CIPHERTEXT


def test_header_line_matches_exactly_after_normalizing():
    """The precise condition ReadBlockFromFile() tests, and the precise
    reason a Windows-written ciphertext decoded to nothing on Linux."""
    windows_header = CRLF_CIPHERTEXT.split(b"\n")[0]
    assert windows_header != b"-----BEGIN CIPHERTEXT-----"

    normalized_header = abe._lf(CRLF_CIPHERTEXT).split(b"\n")[0]
    assert normalized_header == b"-----BEGIN CIPHERTEXT-----"


def test_a_lone_cr_is_not_touched():
    """Only the CRLF pair is a line ending. A bare \\r inside the payload
    would be part of the data, and rewriting it would corrupt it."""
    assert abe._lf(b"ab\rcd") == b"ab\rcd"


def test_normalizing_is_idempotent():
    assert abe._lf(abe._lf(CRLF_CIPHERTEXT)) == abe._lf(CRLF_CIPHERTEXT)
