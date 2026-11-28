# Configuration

reviewloop works with no configuration. To change its behavior, commit `.reviewloop.yml` at the repository root.

- It is read from the **target branch** of the pull request, never from the PR branch, so a pull request cannot change
  how it is reviewed.
- Unknown keys are errors. A key that looks like a secret (`token`, `secret`, `password`, `api_key`) is rejected:
  credentials come only from CI variables.

## Full example

```yaml
version: 1
agent: claude-code        # claude-code | codex | cursor
model: sonnet             # passed to the agent CLI as is
language: en              # language of review comments
paths:
  exclude: ["**/*.snap", "docs/generated/**"]
rules:
  - path: "app/Http/**"
    file: .reviewloop/rules/api.md
tracker:
  type: jira              # github | gitlab | jira
  url: https://jira.example.com
  key_pattern: "[A-Z]+-\\d+"
  require_context: false
security:
  enabled: true
  scanners: [opengrep, gitleaks]
suggestions: true
fail_on: none             # none | critical | high
large_diff_lines: 3000
review_drafts: false
```

## Keys

| Key | Default | Meaning |
| --- | --- | --- |
| `version` | `1` | Config schema version |
| `agent` | `claude-code` | Agent CLI. `codex` and `cursor` also need `REVIEWLOOP_EXPERIMENTAL_AGENTS=1` |
| `model` | none | Model passed to the CLI; none uses the CLI default |
| `language` | `en` | Language of review comments, for example `de` or `ja` |
| `paths.exclude` | `[]` | Globs of files never sent to the agent, on top of the built-in list |
| `rules` | `[]` | Repository rule files, each added when a changed file matches its `path` glob |
| `tracker.type` | none | Where tickets live: `github`, `gitlab` or `jira` |
| `tracker.url` | none | Jira base URL (Jira only) |
| `tracker.key_pattern` | per tracker | Regex for ticket keys in the PR title, body and branch |
| `tracker.require_context` | `false` | Skip the review when no ticket is found |
| `security.enabled` | `true` | Run the security review branch |
| `security.scanners` | `[opengrep, gitleaks]` | Scanners run before the security agents, when installed |
| `suggestions` | `true` | Attach code suggestions where the platform supports them |
| `fail_on` | `none` | Exit `1` when a finding of an initial review is at or above this severity. A recheck exits `0` or `2` only, unless no earlier review exists and it runs a full review instead |
| `large_diff_lines` | `3000` | Above this many changed lines the diff is split into groups |
| `review_drafts` | `false` | Review draft pull requests when they open |

## Excluded files

These files are listed in the summary as `excluded` with a reason and never reach the agent: binary files, deleted
files, rename-only and mode-only changes, lockfiles (`*.lock`, `package-lock.json`, `pnpm-lock.yaml`, `go.sum`, ...),
generated or vendored paths (`vendor/**`, `node_modules/**`, `dist/**`, `build/**`, `*.min.js`, `*_pb2.py`, ...) and
`paths.exclude`.

## Repository rules

A rule file is Markdown with one rule per line, for example:

```markdown
- API-1 [high] A controller action returns an Eloquent model directly. It leaks hidden columns. Return a resource.
```

Rule files are read from the target branch. They are added on top of the built-in rules for the detected stacks, and
a repository rule wins when it conflicts with a built-in one. reviewloop prefixes every rule id with `REPO-`
(`API-1` becomes `REPO-API-1`), so findings never mix up a repository rule with a built-in id such as `PY-1`.

## Scanners

`security.scanners` runs gitleaks (on the PR's commits) and opengrep (on the changed files) before the security
agents. Their output is a hint for the agents; a missing scanner is skipped with one log line.

opengrep never downloads rules. It runs with `--config <rules directory> --disable-version-check`:

- In the image the rules are at `/opt/opengrep-rules`: the `opengrep/opengrep-rules` repository pinned to one commit
  (in `COMMIT` there, checked by sha256 at build time), limited to C#, Go, Java, JavaScript, PHP, Python, TypeScript
  and the generic rules, without the secret rules (gitleaks covers those). Their license is in `LICENSE` there.
- Outside the image set `REVIEWLOOP_OPENGREP_RULES` to a directory of opengrep rules. When it is unset and
  `/opt/opengrep-rules` does not exist, opengrep is skipped; reviewloop never falls back to `--config auto`.

## Tracker

| `tracker.type` | Credentials | Default `key_pattern` |
| --- | --- | --- |
| `github` | the run's `GITHUB_TOKEN` | `#\d+` |
| `gitlab` | `REVIEWLOOP_GITLAB_TOKEN` | `#\d+` |
| `jira` | `REVIEWLOOP_JIRA_TOKEN`; Jira Cloud (`*.atlassian.net`) also `REVIEWLOOP_JIRA_EMAIL` | `\b[A-Z][A-Z0-9]+-\d+\b` |

Jira Cloud uses an API token with the account email. Jira Data Center uses a personal access token.

## Environment variables

| Variable | Meaning |
| --- | --- |
| `REVIEWLOOP_GITLAB_TOKEN` | GitLab project access token (GitLab only) |
| `REVIEWLOOP_BITBUCKET_TOKEN` | Bitbucket access token (Bitbucket only) |
| `REVIEWLOOP_JIRA_TOKEN`, `REVIEWLOOP_JIRA_EMAIL` | Jira tracker credentials |
| `REVIEWLOOP_EXPERIMENTAL_AGENTS=1` | Allow the experimental agents `codex` and `cursor` |
| `REVIEWLOOP_KEEP_WORKDIR=1` | Keep the work directory (agent inputs and transcripts) for debugging |

Agent credentials are listed on each [agent page](agents/claude-code.md).
