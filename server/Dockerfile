# Builds the MCP server on top of your OpenABE fork (Claudiocli/openabe),
# compiled from source for Linux since openabe/cli's .exe files are
# Windows-only. The four workarounds below are for build-script/
# environment issues found while verifying this fork builds cleanly on
# Ubuntu 24.04 - none are in the ported cryptography itself. Full
# evidence for each is in docs/phase2-engineering-notes.md.

FROM ubuntu:24.04 AS openabe-builder

# Bug A: deps/install_pkgs.sh's main_ubuntu() installs the long-removed
# "python-pip" package and hard-fails if a bare `pip` binary isn't found
# afterward, aborting before installing any real build tool. Install
# python3-pip (+ a pip symlink) and build-essential ourselves first so
# that check passes and `make`/gcc/g++ are actually present. `sudo` is
# also missing from a bare Ubuntu image (nothing needs it when already
# root) but install_pkgs.sh's install_package() calls it unconditionally.
RUN apt-get update && apt-get install -y --no-install-recommends \
    git ca-certificates build-essential python3-pip sudo && \
    ln -sf /usr/bin/pip3 /usr/bin/pip && \
    rm -rf /var/lib/apt/lists/*

RUN git config --global --add safe.directory '*' && \
    git clone --depth 1 https://github.com/Claudiocli/openabe.git /openabe

WORKDIR /openabe

# Bug B: deps/gtest/Makefile's ".built" recipe does `cd $<;` where $< is
# an order-only prerequisite - it resolves empty on this Make (GNU Make
# 4.3), sending the shell to $HOME instead of the extracted gtest source.
RUN sed -i 's|cd \$<;|cd googletest-release-$(VERSION);|' deps/gtest/Makefile

# Bug C: deps/relic/Makefile unconditionally calls $(MINGW_MAKE), which
# Makefile.common only defines on the Windows branch. Use GNU Make's own
# $(MAKE), always correctly set regardless of platform.
RUN sed -i 's|\$(MINGW_MAKE)|$(MAKE)|g' deps/relic/Makefile

RUN bash deps/install_pkgs.sh
RUN bash -c "source ./env && make"

# Bug D: OpenSSL's own build (./config) installs its shared libraries
# into deps/root/lib64/ on this distro; env only ever adds .../lib to
# LD_LIBRARY_PATH (correct for MSYS2/Windows, not here).
ENV LD_LIBRARY_PATH=/openabe/deps/root/lib:/openabe/deps/root/lib64:/openabe/root/lib:/openabe/root/lib64

# Sanity check inside the build: the CLI tools must actually run, not
# just link, before this stage is considered done.
RUN cli/oabe_setup -s CP -p smoketest && test -f smoketest.mpk.cpabe


FROM python:3.12-slim AS runtime

# Runtime counterpart of libgmp-dev/libglib2.0-dev used at build time -
# GMP is a real runtime dependency of the CP-ABE math; glib is included
# defensively and can be dropped if unneeded (verify with `ldd`).
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgmp10 libglib2.0-0 && \
    rm -rf /var/lib/apt/lists/*

COPY --from=openabe-builder /openabe/deps/root /opt/openabe/deps/root
COPY --from=openabe-builder /openabe/root /opt/openabe/root
COPY --from=openabe-builder /openabe/cli/oabe_setup /openabe/cli/oabe_keygen /openabe/cli/oabe_enc /openabe/cli/oabe_dec /opt/openabe/bin/

ENV PATH=/opt/openabe/bin:$PATH
ENV LD_LIBRARY_PATH=/opt/openabe/deps/root/lib:/opt/openabe/deps/root/lib64:/opt/openabe/root/lib:/opt/openabe/root/lib64

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py schema.sql ./

# One shared server instance for every agent product and installation -
# no per-agent identity baked in here (see core.resolve_agent()).
# PABEL_DB_DSN, KEYCLOAK_URL, REALM, CLIENT_ID come from the environment,
# never baked in.
CMD ["python", "mcp_server.py"]
