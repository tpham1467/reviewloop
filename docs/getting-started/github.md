# GitHub

reviewloop runs as a GitHub Action in your own workflow, with the `GITHUB_TOKEN` of the run and your own agent
credential.

## Setup

1. Add a repository secret `ANTHROPIC_API_KEY` (**Settings > Secrets and variables > Actions**). Other credentials
   are listed in [Claude Code](../agents/claude-code.md).
2. Copy [`examples/github-workflow.yml`](https://github.com/tpham1467/reviewloop/blob/main/examples/github-workflow.yml)
   to `.github/workflows/reviewloop.yml`:

```yaml
name: reviewloop

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
    timeout-minutes: 60
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          ref: ${{ github.event.pull_request.base.ref || github.event.repository.default_branch }}
          fetch-depth: 0
          persist-credentials: false
      - name: Fetch the pull request head
        env:
          PR_NUMBER: ${{ github.event.pull_request.number || github.event.issue.number }}
          GH_TOKEN: ${{ github.token }}
        run: |
          auth=$(printf 'x-access-token:%s' "$GH_TOKEN" | base64 -w0)
          echo "::add-mask::$auth"
          git -c "http.https://github.com/.extraheader=Authorization: Basic $auth" \
            fetch --no-tags origin "+refs/pull/${PR_NUMBER}/head:refs/remotes/pull/${PR_NUMBER}/head"
      - uses: tpham1467/reviewloop@v0
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
```

3. Optional: commit a [`.reviewloop.yml`](../configuration.md) on the default branch.

Open a pull request. The job posts a "reviewing" summary comment, then inline findings, then the final summary.

## What the workflow does

- **Triggers.** An opened, reopened or ready-for-review pull request starts a review. A push does not (no
  `synchronize`: with `cancel-in-progress`, every push would cancel a running review); comment `/reviewloop recheck`
  after fixing. Draft pull requests are skipped until they are marked ready (`review_drafts: true` changes that).
- **Comment filter.** The job runs only for `/reviewloop` comments whose `author_association` is `OWNER`, `MEMBER` or
  `COLLABORATOR`. This only narrows who can start a run; it is not a permission check. `MEMBER` is any member of the
  organization that owns the repository, and `COLLABORATOR` includes read-only collaborators. Inside the run, reviewloop
  decides whether to accept the command (see [Commands](#commands)).
- **Concurrency** is set on the job, not the workflow, so it applies only to runs that pass the `if`. A workflow-level
  group would let any comment, even "LGTM", cancel a running review. These still cancel a running review on the same
  pull request: a `/reviewloop` comment from any `OWNER`, `MEMBER` or `COLLABORATOR`, including `/reviewloop help` and
  a command reviewloop then refuses, and the `reopened` and `ready_for_review` events.
- **Checkout.** The job checks out the trusted base branch and fetches the pull request head as git objects only.
  `persist-credentials: false` keeps the token out of `.git/config`; the fetch passes it on that one command.
- **Token.** The run's `GITHUB_TOKEN` with `contents: read` and `pull-requests: write` reads the pull request and
  posts comments. reviewloop never pushes.
- **Artifacts.** `reviewloop.json` and `reviewloop.sarif` are written to the workspace. The example uploads them.

## Commands

Comment on the pull request:

| Command | Effect |
| --- | --- |
| `/reviewloop review` | Review the whole pull request again |
| `/reviewloop recheck` | Re-check open findings against new commits and replies; resolve fixed threads |
| `/reviewloop help` | Post the command list |

reviewloop accepts a command when its permission check passes. For a comment, the check uses the comment's
`author_association`: `OWNER`, `MEMBER` or `COLLABORATOR`. That is broader than write access: it includes every member
of the owning organization and read-only collaborators. Any other comment exits within seconds without calling an
agent.

## Fork pull requests

`pull_request` runs from forks get no secrets and a read-only token, so reviewloop skips fork pull requests when they
open. A maintainer runs `/reviewloop review` on the pull request; that `issue_comment` run uses the base repository's
secrets and workflow file, and reads the fork's code as data only.

Do not switch the workflow to `pull_request_target` to review forks automatically: it hands secrets to a run that
processes untrusted code without a maintainer looking first.

## Action inputs

| Input | Default | Meaning |
| --- | --- | --- |
| `agent` | `claude-code` | Agent CLI. `codex` and `cursor` are experimental |
| `model` | empty | Model name passed to the CLI as is |
| `config-path` | `.reviewloop.yml` | Config file path on the target branch |
| `github-token` | `${{ github.token }}` | Token for the GitHub API |
| `version` | the release's pinned version | Image tag to run instead |

Each action release runs one exact image version, `ghcr.io/tpham1467/reviewloop:<x.y.z>`. `@v0` moves to the latest
`0.x` release; pin `@v0.1.0` or a commit SHA to stay on one version.

The action passes agent credentials to the container by name only: `ANTHROPIC_*`, `CLAUDE_CODE_*`, the Bedrock and
Vertex variables, `OPENAI_API_KEY`, `CODEX_AUTH_JSON`, `CURSOR_API_KEY` and `REVIEWLOOP_*`, when you set them in the
step's `env`.

## Fail the check on findings

Set `fail_on: high` (or `critical`) in `.reviewloop.yml`. Exit codes: `0` success, `1` findings at or above
`fail_on`, `2` infrastructure error (agent failed, timeout, a file could not be reviewed).
