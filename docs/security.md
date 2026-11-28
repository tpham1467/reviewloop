# Security

reviewloop runs in your CI with your credentials. Nothing is sent to the reviewloop project: no code, no keys, no
review data. The only network calls are your agent CLI's calls to its model provider, the VCS API and, when
configured, your tracker.

## Who holds which credential

| Credential | Held by | Never reaches |
| --- | --- | --- |
| VCS token (`GITHUB_TOKEN`, `REVIEWLOOP_GITLAB_TOKEN`, ...) | reviewloop | the agent |
| Tracker token (`REVIEWLOOP_JIRA_TOKEN`) | reviewloop | the agent |
| Model credential (`ANTHROPIC_API_KEY`, ...) | the agent CLI process | commands the agent runs, comments, logs |

The agent cannot post to the pull request. It writes one JSON file; reviewloop validates it, anchors each finding
and posts it.

## Agent isolation

Enforced by reviewloop around every agent run, whatever the CLI does:

- The agent works in a fresh copy of the checkout with no remotes, no git credentials, no hooks, no symlinks and no
  agent config from the repository (`.claude/`, `.codex/`, `.cursor/`, `.mcp.json`, ...).
- Before the first agent run, reviewloop removes credentials from the CI checkout's own `.git/config`: remote URL
  userinfo, `http.*.extraheader`, credential helpers and URL rewrites.
- The agent process gets a scrubbed environment: its own model credential, `PATH`, `HOME` and locale only. `HOME` is a
  throwaway directory per run.
- `git` on the agent's `PATH` is a guard that allows only `git log`, `git blame`, `git show` and `git diff`, with paths
  limited to the copy.
- After each run, any change in the copy other than the output file fails the step.
- Every text reviewloop posts or logs goes through secret redaction.

How each CLI is restricted, and why Codex and Cursor are experimental, is in
[ADR 0002](adr/0002-agent-tool-restriction.md).

## Untrusted pull requests

- Config and repository rule files are read from the target branch only.
- PR text, ticket text, thread replies and the diff reach the agent as files, never inside its instructions, and the
  agent is told to treat them as data.
- Commands run only for users with write access, read from the API: on GitHub write, maintain or admin permission on
  the repository (the comment's `author_association` is not trusted); on GitLab the Developer role or higher.
- Fork pull requests are not reviewed automatically. A maintainer runs `/reviewloop review`.

## CI setup checklist

- **GitHub:** keep `persist-credentials: false` on `actions/checkout`. Give the job `contents: read` and
  `pull-requests: write` only. Do not use `pull_request_target`.
- **GitLab:** store tokens as masked variables of type **Variable**.
- Use an API key, cloud credentials or OIDC for team pipelines. Never share a personal subscription token
  (`CLAUDE_CODE_OAUTH_TOKEN`, a ChatGPT `auth.json`) across a team.
- Pin the action and image to a version you have reviewed.

### GitLab File-type variables

GitLab writes File-type variables as plain files under `<builds>/<project>.tmp/`, next to the checkout, for the whole
job. reviewloop cannot remove them, because the job may need them. An agent whose read tools can open absolute paths
(Codex and Cursor in milestone 1) could read them. Claude Code blocks reads outside its working copy. Store tokens as
**Variable**, not **File**.

## Image

`ghcr.io/tpham1467/reviewloop` is built from the repository's `Dockerfile`:

- base images pinned by digest; gitleaks and opengrep pinned by version and sha256; Node and the agent CLIs pinned by
  exact version;
- runs as a non-root user (uid 1001), also in the GitLab component. reviewloop trusts exactly the CI checkout path
  for its git calls (`safe.directory`), never `*` and never the agent sandbox. The GitHub Action runs the image as the
  runner's user, with all capabilities dropped and `no-new-privileges`;
- release images are signed with cosign and carry an SBOM and SLSA provenance.

## Reporting a vulnerability

See [SECURITY.md](https://github.com/tpham1467/reviewloop/blob/main/SECURITY.md). Do not open a public issue.
