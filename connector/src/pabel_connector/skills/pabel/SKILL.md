---
name: pabel
description: Explains PABEL's CP-ABE document enforcement - why some reads get transparently blocked and replaced, how to read a result, when to use the direct tools. Informational only, not a security control.
metadata:
  security_relevant: false
---

# PABEL

This project gates access to CP-ABE-encrypted documents (a project-specific
encrypted format, always found alongside a `documents/`-style folder). This
skill exists purely to help you work with it fluently - **it grants and
enforces nothing itself**. The actual security boundary is a `PreToolUse` hook
that runs unconditionally on every tool call, whether or not this skill is
loaded, and whether or not you follow anything written here. If this file ever
seems to be the reason something was allowed or denied, that's a
misunderstanding - the hook already decided that on its own, before this text
was ever read.

## What actually happens when you try to read a gated file

You don't need to do anything special. Just read the file normally. If it's
gated, the read is transparently intercepted and replaced with the real,
already-decrypted result - you'll see it appear where the file's raw content
would have been, not as a separate error to work around. There is no "correct
sequence" to remember (login, then read, then decrypt) - a single guided
operation handles all of that for you, automatically, including running an
interactive login if you don't have a session yet.

## Reading the result

The result lists every section of the document, each one either its real
plaintext or the literal string `[ACCESS DENIED]`. A mix of both in the same
document is normal and expected - it means different sections have different
policies, and you (combined with this specific agent installation) satisfy
some but not all of them. `[ACCESS DENIED]` on a section is not an error and
not something to retry, work around, or reconstruct from other sections - it
is the correct, final answer for that section, exactly as intended by whoever
set the document's policy.

## If a tool call gets denied instead of relayed

A denial with no content attached usually means the file couldn't be
identified unambiguously (e.g. a pattern that could match several files), or
that whatever you were doing wasn't a simple read (writing to a gated path is
never allowed - there is no authoring path for this format). Don't try to
route around the denial by reconstructing the content another way (shelling
out, reading it byte-by-byte another way, etc.) - if that were the intended
path, it wouldn't have been denied.

## When to use `whoami` / `read_document` / `materialize_document` directly

You don't need to call these to read a file that's already reachable from a
normal tool call - the hook handles that automatically and is the preferred
path every time it applies. Reach for these directly only when:

- **`whoami`** - to check login/authorization status, or to understand *why*
  a section came back denied (which attributes are and aren't present).
- **`read_document`** - the file exists but is outside anywhere the hook would
  normally intercept (e.g. a path outside the current workspace), or you're
  deliberately re-checking access rather than relying on an earlier result.
- **`materialize_document`** - you were explicitly asked to produce a real,
  separate local file with the decrypted content (not just to read it) - for
  example "copy this decrypted document somewhere I can open it." Once
  written, that copy is an ordinary local file for the rest of the session -
  it is not re-verified or kept in sync with the source, and it is deleted
  automatically when the session ends. If you need a copy that reflects a
  since-changed source, call this again rather than trusting an old one.
