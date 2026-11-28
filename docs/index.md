# reviewloop

reviewloop reviews pull requests and merge requests from your own CI, with your own coding-agent CLI and your own
credentials. It posts inline comments on the right lines, a summary, and optional code suggestions, then re-checks
open threads when you ask.

- **Your CI, your key.** No server, no account, no data sent to the project. Only your agent CLI talks to its model
  provider.
- **Your agent.** Claude Code is supported; Codex and Cursor are experimental in milestone 1.
- **The agent never holds the VCS token.** It reads a sandboxed copy, runs four read-only git commands and writes one
  JSON file. reviewloop validates and posts.

## Start

- [GitHub](getting-started/github.md): one workflow file and one secret.
- [GitLab](getting-started/gitlab.md): one component include, two variables, and a webhook for commands.
- [Bitbucket Cloud](getting-started/bitbucket-cloud.md) and
  [Bitbucket Data Center with Jenkins](getting-started/bitbucket-dc-jenkins.md): milestone 1.5 previews.

## How a review runs

1. A pull request opens, or an allowed user comments `/reviewloop review` (see
   [Security](security.md#untrusted-pull-requests)).
2. reviewloop computes the diff, reads `.reviewloop.yml` and rule files from the target branch, and fetches linked
   tickets.
3. The agent reviews the diff in a sandbox; an optional security branch runs opengrep, gitleaks and two security
   agents in parallel.
4. A quality gate drops findings the diff disproves and rewrites vague ones.
5. reviewloop anchors each finding on its quoted code, posts it, and updates the summary with a score and per-file
   coverage. No file is skipped silently: each one ends `reviewed`, `excluded` or `failed`.

`/reviewloop recheck` later decides per thread `fixed`, `not_fixed`, `valid_rebuttal` or `no_response`, replies, and
resolves the fixed ones. State lives in hidden markers in the comments; there is no database.

## More

- [Configuration](configuration.md)
- [Security model](security.md)
- [FAQ](faq.md)
