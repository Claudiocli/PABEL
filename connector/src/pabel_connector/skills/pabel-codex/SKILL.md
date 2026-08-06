---
name: pabel
description: Explains PABEL's CP-ABE document enforcement for Codex CLI / ChatGPT desktop - why a raw read of a gated file returns meaningless ciphertext here (no interception exists for these products), and which tool to call instead. Informational only, not a security control.
---

# PABEL

This project gates access to CP-ABE-encrypted documents (a project-specific
encrypted format, always found alongside a `documents/`-style folder). Read
this before you first encounter one.

## The one fact this skill exists to tell you

**Unlike some other agent products, nothing here automatically intercepts or
relays a read of a gated file.** If you open one with a normal file-read tool,
you get back real, meaningless CP-ABE ciphertext - not a denial, not an error,
not a hint to try something else. It will look like garbage bytes, not text.
If you see that, it almost always means you read the file directly instead of
calling the tool below - it does not mean the file is corrupt or that access
is impossible.

## What to do instead: call `read_document` yourself

To actually get a document's content, call the `read_document` tool
explicitly with its path - never `cat`/open/read it as a normal file.
`read_document` re-verifies your identity and this installation's own
credentials on every call and returns the real, already-decrypted result.
Running an interactive login for you if you don't have a session yet is part
of the same call - there's no separate "log in first" step to remember.

## Reading the result

The result lists every section of the document, each one either its real
plaintext or the literal string `[ACCESS DENIED]`. A mix of both in the same
document is normal and expected - it means different sections have different
policies, and you (combined with this specific agent installation) satisfy
some but not all of them. `[ACCESS DENIED]` on a section is not an error and
not something to retry, work around, or reconstruct from other sections - it
is the correct, final answer for that section, exactly as intended by whoever
set the document's policy. Don't try to route around a denial by
reconstructing the content another way (shelling out, reading the raw bytes,
guessing from context) - if that were the intended path, it wouldn't have
been denied.

## The other two tools

- **`whoami`** - check login/authorization status, or understand *why* a
  section came back denied (which attributes are and aren't present).
- **`materialize_document`** - only when explicitly asked to produce a real,
  separate local file with the decrypted content (not just to read it) - for
  example "copy this decrypted document somewhere I can open it." That copy
  is an ordinary local file for the rest of the session once written - it is
  not re-verified or kept in sync with the source. If you need a copy that
  reflects a since-changed source, call this again rather than trusting an
  old one.

## What this skill is not

This is guidance, not a gate - nothing enforces that you follow it, and there
is no security boundary behind this text. A user relying on this project for
confidentiality is relying on the CP-ABE ciphertext itself staying
meaningless without server-derived key material (true regardless of whether
this skill is loaded or followed), not on any agent choosing to call
`read_document` instead of reading the file directly.
