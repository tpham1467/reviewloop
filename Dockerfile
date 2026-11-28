# syntax=docker/dockerfile:1@sha256:572cebbc2b15e6f84c077742952674889cb63cd5e9b80abdffcbe587b6f19183
# reviewloop CI image: reviewloop, git, gitleaks, opengrep, Node 22, Claude Code and Codex.
# Every external input is pinned: base images by digest, binaries by sha256, npm packages by exact version.
# The Cursor CLI is not included: see docs/agents/cursor.md for a derived image.

ARG PYTHON_IMAGE=python:3.12-slim@sha256:191083cdf6c7bda8c76158983163ead5d5cf7b77cf3033e805bb0f5296f51b4b
ARG NODE_IMAGE=node:22.23.5-trixie-slim@sha256:f0960f8dc047f358e3fb428f36ccba21732194fa5f0cac849f401e63e62fb9e6
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.11.19@sha256:b46b03ddfcfbf8f547af7e9eaefdf8a39c8cebcba7c98858d3162bd28cf536f6
ARG TARGETARCH

FROM ${UV_IMAGE} AS uv

# Scanner binaries per architecture, each checked against its release sha256.
FROM ${PYTHON_IMAGE} AS tools-amd64
ADD --checksum=sha256:551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb \
    https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_linux_x64.tar.gz /tmp/gitleaks.tar.gz
ADD --checksum=sha256:46e8133cc490f9f50518061590ce2f6be5a08afaeda034471c0ceda4b3899808 \
    https://github.com/opengrep/opengrep/releases/download/v1.33.1/opengrep_manylinux_x86 /tmp/opengrep

FROM ${PYTHON_IMAGE} AS tools-arm64
ADD --checksum=sha256:e4a487ee7ccd7d3a7f7ec08657610aa3606637dab924210b3aee62570fb4b080 \
    https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_linux_arm64.tar.gz /tmp/gitleaks.tar.gz
ADD --checksum=sha256:d1433f63a90b8bc0b6ad4b498a13b372b856ce9a754a43484ba4920fe2d76dcf \
    https://github.com/opengrep/opengrep/releases/download/v1.33.1/opengrep_manylinux_aarch64 /tmp/opengrep

FROM tools-${TARGETARCH} AS tools
RUN mkdir /out \
    && tar -xzf /tmp/gitleaks.tar.gz -C /out gitleaks \
    && install -m 0755 /tmp/opengrep /out/opengrep

# opengrep rules pinned to a commit of opengrep/opengrep-rules and checked by sha256, so a scan makes no network call
# (scanners pass `--config /opt/opengrep-rules`, never `auto`). Only the languages of the built-in stacks plus the
# generic rules; generic/secrets is left out because gitleaks covers secrets. Test fixtures (non-YAML files and
# *.test.yaml) are dropped: opengrep would try to load every YAML file in the directory as rules.
FROM ${PYTHON_IMAGE} AS rules
ARG OPENGREP_RULES_COMMIT=653482b25649be02d8cc373b88abb071949c352b
ADD --checksum=sha256:2de100286d4379cf38f5c12e0e4b360b35b8799218ce5cf58bd8e24750f04f40 \
    https://codeload.github.com/opengrep/opengrep-rules/tar.gz/${OPENGREP_RULES_COMMIT} /tmp/rules.tar.gz
RUN mkdir /tmp/src /out \
    && tar -xzf /tmp/rules.tar.gz -C /tmp/src --strip-components=1 \
    && cd /tmp/src \
    && find csharp go java javascript php python typescript generic -path generic/secrets -prune -o -type f \
         \( -name '*.yaml' -o -name '*.yml' \) ! -name '*.test.yaml' ! -name '*.test.yml' -print \
         | xargs cp --parents -t /out \
    && cp LICENSE /out/LICENSE \
    && echo "${OPENGREP_RULES_COMMIT}" > /out/COMMIT

# Agent CLIs. The Claude Code postinstall picks the native binary for this platform.
FROM ${NODE_IMAGE} AS agents
RUN npm install --global --prefix /opt/agents --no-fund --no-audit \
        @anthropic-ai/claude-code@2.1.332 \
        @openai/codex@0.172.0 \
    && npm cache clean --force

# reviewloop and its locked dependencies, installed from the build context.
FROM ${PYTHON_IMAGE} AS app
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/reviewloop \
    UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /src
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project
COPY README.md LICENSE ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

FROM ${PYTHON_IMAGE}
LABEL org.opencontainers.image.title="reviewloop" \
      org.opencontainers.image.description="AI code review in your own CI with your own agent CLI" \
      org.opencontainers.image.source="https://github.com/tpham1467/reviewloop" \
      org.opencontainers.image.licenses="AGPL-3.0-only"

RUN apt-get update \
    && apt-get install --yes --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    # uid 1001 is the GitHub-hosted runner user, so files the job writes into the workspace keep their owner.
    && useradd --uid 1001 --user-group --create-home --home-dir /home/reviewloop --shell /bin/bash reviewloop \
    # The GitHub Action runs the container as the runner's uid, which may differ: HOME stays writable for any uid.
    && chmod 1777 /home/reviewloop

COPY --from=tools /out/ /usr/local/bin/
COPY --from=rules /out/ /opt/opengrep-rules/
COPY --from=agents /usr/local/bin/node /usr/local/bin/node
COPY --from=agents /opt/agents /opt/agents
COPY --from=app /opt/reviewloop /opt/reviewloop

# Runs as uid 1001. reviewloop runs git with global and system config disabled and trusts exactly the CI checkout
# path through safe.directory, never `*` and never the agent sandbox. See docs/faq.md.
ENV PATH=/opt/reviewloop/bin:/opt/agents/bin:${PATH} \
    HOME=/home/reviewloop \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER 1001
# `docker run <image>` runs `reviewloop run`; `docker run <image> version` (or review, recheck, init) runs that command.
ENTRYPOINT ["reviewloop"]
CMD ["run"]
