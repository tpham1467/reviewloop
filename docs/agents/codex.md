# Codex (experimental)

Codex is **experimental** in milestone 1. It cannot hold shell commands to the review allowlist (`git log`,
`git blame`, `git show`, `git diff`): in its read-only sandbox any read-only command runs, and its reads are not limited
to the working copy. Writes, network and the post-run snapshot check still hold. The reasons are in
[ADR 0002](../adr/0002-agent-tool-restriction.md); the full adapter is planned for milestone 1.5.

## Enable

```yaml
# .reviewloop.yml
agent: codex
model: gpt-5-codex   # optional, passed to `codex exec --model` as is
```

and set `REVIEWLOOP_EXPERIMENTAL_AGENTS=1` as a CI variable. Without it the run fails with a configuration error naming
the variable. On GitHub, also pass `agent: codex` to the action or set it in the step's `env`.

The image ships `@openai/codex` at an exact version.

## Credentials

| Variable | Use |
| --- | --- |
| `OPENAI_API_KEY` | OpenAI API key. Recommended for teams |
| `CODEX_AUTH_JSON` | The full contents of `~/.codex/auth.json` from a ChatGPT login. One person's own repositories only |

Only one model credential reaches the Codex process; commands Codex runs get `PATH` and `HOME` only.

!!! warning "`auth.json` refresh tokens rotate"
    `CODEX_AUTH_JSON` holds a refresh token. Codex rotates it when it refreshes the session, and reviewloop writes the
    file into a throwaway `HOME` that is deleted after each run, so the new token is lost. Once the stored token is
    rotated or expires, runs fail with an authentication error. Log in again locally and save the new `auth.json` as
    the variable. Use `OPENAI_API_KEY` to avoid this.

A ChatGPT login is personal: never share one person's `auth.json` across a team's pipelines.
