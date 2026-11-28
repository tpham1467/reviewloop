# Cursor (experimental)

Cursor is **experimental** in milestone 1. Its command allowlist can be bypassed (it drops `VAR=value` prefixes when
matching, so `PATH=... git diff ...` passes as `git diff`), and with its sandbox off its read tools can open absolute
paths outside the working copy. reviewloop still fails any run that changes a file other than its output. The details
are in [ADR 0002](../adr/0002-agent-tool-restriction.md); the full adapter is planned for milestone 1.5.

## Enable

```yaml
# .reviewloop.yml
agent: cursor
model: sonnet-4   # optional, passed to `cursor-agent --model` as is
```

and set `REVIEWLOOP_EXPERIMENTAL_AGENTS=1` and `CURSOR_API_KEY` (a Cursor API key) as CI variables. A
`cursor-agent login` session is not used.

## Image

The reviewloop image does not include the Cursor CLI: its official installer is a script that always fetches the
latest build, which cannot be pinned. Build a derived image that pins one build by URL and checksum:

```dockerfile
FROM ghcr.io/tpham1467/reviewloop:0
# One Cursor CLI build. Get the URL from the official installer (https://cursor.com/install) and compute the
# checksum yourself: `curl -fsSL <url> | sha256sum`.
ADD --chown=1001:1001 \
    --checksum=sha256:7663b1c5d1b56e095ef8e23bef38d66df6cfd665c4873470214e0e411f0f97b3 \
    https://downloads.cursor.com/lab/2026.11.22-e9042b2/linux/x64/agent-cli-package.tar.gz /tmp/cursor.tar.gz
USER root
RUN mkdir -p /opt/cursor-agent \
    && tar -xzf /tmp/cursor.tar.gz --strip-components=1 -C /opt/cursor-agent \
    && rm /tmp/cursor.tar.gz \
    && ln -s /opt/cursor-agent/cursor-agent /usr/local/bin/cursor-agent
USER 1001
```

The URL and checksum above are for linux/x64, build `2026.11.22-e9042b2`. Use `linux/arm64` in the URL on arm64.

Push the image to your registry, then use it:

- GitHub: `uses: tpham1467/reviewloop@v0` only runs the official image. Run your image with `docker run` in a step
  instead, mirroring the steps of [`action.yml`](https://github.com/tpham1467/reviewloop/blob/main/action.yml).
- GitLab: copy the job from the component and change `image:`.
