# reviewloop

AI code review for pull and merge requests that runs in your own CI, with your own coding-agent CLI and your own
credentials. It posts inline comments anchored on the right lines, a summary with per-file coverage, and optional code
suggestions, then re-checks open threads on request.

No server, no account, no telemetry. The only external calls are your agent CLI's calls to its model provider.

**Status:** pre-release (`0.x`). Docs: <https://tpham1467.github.io/reviewloop/>

## Quickstart: GitHub

1. Add the repository secret `ANTHROPIC_API_KEY`.
2. Copy [`examples/github-workflow.yml`](examples/github-workflow.yml) to `.github/workflows/reviewloop.yml`. Its core:

```yaml
on:
  pull_request:
    types: [opened, reopened, ready_for_review]
  issue_comment:
    types: [created]
permissions:
  contents: read
  pull-requests: write
jobs:
  review:
    if: >-
      github.event_name == 'pull_request' ||
      (github.event.issue.pull_request && startsWith(github.event.comment.body, '/reviewloop') &&
       contains(fromJSON('["OWNER","MEMBER","COLLABORATOR"]'), github.event.comment.author_association))
    concurrency:
      group: reviewloop-${{ github.event.pull_request.number || github.event.issue.number }}
      cancel-in-progress: true
    runs-on: ubuntu-latest
    steps:
      # checkout of the base branch + fetch of the PR head: copy them from the example file
      - uses: tpham1467/reviewloop@v0
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
```

3. Open a pull request. Comment `/reviewloop recheck` after pushing fixes.

Details: [docs/getting-started/github.md](docs/getting-started/github.md).

## Quickstart: GitLab

1. Create a project access token (role Developer, scope `api`).
2. Add masked CI/CD variables of type **Variable** (not File): `REVIEWLOOP_GITLAB_TOKEN` and `ANTHROPIC_API_KEY`.
3. Add to `.gitlab-ci.yml`:

```yaml
include:
  - component: $CI_SERVER_FQDN/tpham1467/reviewloop/reviewloop@0
```

4. Open a merge request. For `/reviewloop` commands, add a Comments webhook to the pipeline trigger API. Every comment
   then starts a trigger pipeline: exclude your other jobs from `trigger` pipelines first.

Details, including `workflow:rules` and the webhook: [docs/getting-started/gitlab.md](docs/getting-started/gitlab.md).

## How it works

1. A pull request opens, or a user with write access comments `/reviewloop review`.
2. reviewloop computes the diff and reads `.reviewloop.yml`, rule files and linked tickets from the target branch.
3. Your agent CLI reviews the diff in a sandboxed copy and writes JSON; a security branch runs in parallel.
4. A quality gate filters the findings; reviewloop anchors each one on its quoted code and posts it.
5. The summary lists findings by severity, a score and every file as reviewed, excluded or failed.

## Security model

- The agent never sees the VCS or tracker token and cannot post. It gets a credential-free copy of the checkout, a
  scrubbed environment with only its model credential, read tools, `git log|blame|show|diff`, and one output file.
  Any other change fails the run.
- Config and rules come from the target branch; PR text reaches the agent as data. Commands need write access,
  checked through the API. Fork pull requests are reviewed only when a maintainer asks.
- Use an API key, cloud credentials or OIDC for teams. Never share a personal subscription token across a team.

More: [docs/security.md](docs/security.md), [ADR 0002](docs/adr/0002-agent-tool-restriction.md).

## Supported platforms and agents

| Platform | Status |
| --- | --- |
| GitHub (Actions) | Supported |
| GitLab (CI, GitLab.com and self-managed) | Supported |
| Bitbucket Cloud (Pipelines) | Preview (milestone 1.5) |
| Bitbucket Data Center (Jenkins) | Preview (milestone 1.5) |

| Agent CLI | Status | Credential |
| --- | --- | --- |
| Claude Code | Supported | `ANTHROPIC_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN`, Bedrock, Vertex |
| Codex | Experimental (`REVIEWLOOP_EXPERIMENTAL_AGENTS=1`) | `OPENAI_API_KEY`, `CODEX_AUTH_JSON` |
| Cursor | Experimental (`REVIEWLOOP_EXPERIMENTAL_AGENTS=1`), not in the image | `CURSOR_API_KEY` |

## Install outside CI

The image `ghcr.io/tpham1467/reviewloop:0` (or an exact `0.x.y`) contains reviewloop, git, gitleaks, opengrep, Claude Code and Codex.
For your own runner image:

```bash
pipx install reviewloop-ai
reviewloop version
```

The agent CLI and, optionally, gitleaks and opengrep must be on `PATH`. opengrep runs only with a rules directory you
provide in `REVIEWLOOP_OPENGREP_RULES` (see [Configuration](docs/configuration.md#scanners)); without one it is skipped.

## Links

- [Configuration](docs/configuration.md) and [`examples/reviewloop.yml`](examples/reviewloop.yml)
- [Agents](docs/agents/claude-code.md) and [FAQ](docs/faq.md)
- [Contributing](CONTRIBUTING.md), [Code of Conduct](CODE_OF_CONDUCT.md), [Security policy](SECURITY.md),
  [Governance](GOVERNANCE.md), [Changelog](CHANGELOG.md)

## License

AGPL-3.0-only. See [LICENSE](LICENSE). Contributions are accepted under the [CLA](CLA.md).
