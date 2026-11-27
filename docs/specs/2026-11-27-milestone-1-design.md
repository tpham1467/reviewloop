# reviewloop milestone 1: design

Status: approved in review sessions on 2026-11-27, builder check done, pending final read.

## 1. Intent

reviewloop is an open-source AI code reviewer for pull and merge requests. It runs inside the customer's own CI,
uses the customer's own coding-agent CLI (Claude Code, Codex or Cursor) with the customer's own credentials, and
posts inline review comments on the PR. No code, key or review data leaves the customer's infrastructure except the
calls the agent CLI makes to its own model provider.

Why it exists:

- Teams on GitLab self-managed, Bitbucket Data Center and Jenkins have few review bots that run fully in their own
  CI, and none that let them pick the agent CLI per repository.
- Running in the customer's CI with their key is the lowest legal risk: no data processing on our side, no resale
  of model access, no credential handling by a vendor.
- The review method is proven: it is a clean-room rewrite of a bot that reviews thousands of comments per week in
  production. Its pipeline shape is kept; its code, prompts and skill text are not reused.

## 2. Goals, non-goals, anti-goals

Goals for milestone 1:

- Review a PR on GitLab and GitHub from a CI job: inline comments anchored on the right line, a summary comment,
  optional code suggestions.
- Re-check open threads on request (`/reviewloop recheck`) and resolve the fixed ones.
- Large diffs, a security branch and ticket context work.
- Install in under 10 minutes from the README. On GitHub: one workflow file and one secret. On GitLab: one CI
  component include, the agent credential, a project access token, and (for commands) a Comments webhook with a
  trigger token, all documented step by step.

Non-goals for milestone 1 (later milestones):

- Self-hosted server, webhook receiver, database, dashboard (milestone 2, FastAPI + React/Next).
- Bitbucket Cloud, Bitbucket DC + Jenkins (milestone 1.5), other forges after that.
- Skip learning, reports, SBOM/CVE review, shared config across repositories.
- Parallel multi-reviewer mode.

Anti-goals, which make the project a failure even if it works:

- Any text copied from the previous company implementation (code, prompts, skills, rules).
- reviewloop or its maintainers receiving, storing or proxying customer credentials or code.
- The agent being able to post to the VCS, write outside its output file, or read the VCS token.
- A review that silently skips files and still reports success.

## 3. Constraints

- Python 3.11+. License AGPL-3.0, contributions under a CLA.
- English everywhere in the repository. Review comments are written in the language set in config.
- The core never calls an LLM API. All reasoning goes through an agent CLI.
- Credentials come only from CI environment variables.
- Skills and rules respect length limits enforced in CI (section 9).

## 4. Approaches considered

| Approach | Trade-off | Decision |
|---|---|---|
| Port the existing pipeline shape step by step, dropping server-only parts | Keeps a proven flow; more steps to write | **Chosen** |
| Thin orchestrator: a few steps, most logic moved into skills | Less code; loses proven deterministic steps | Rejected |
| One large skill; the agent posts comments through tools | Least code; no anchoring or output validation; agent holds the VCS token | Rejected |

## 5. Pipeline

Steps run in order; steps sharing a group run in parallel and the group waits for every sibling before failing.
Every step reads and writes one `RunContext`. A failed step stops the run, updates the summary comment with the
failure, and the job exits with the infrastructure error code.

### 5.1 Initial review (`/reviewloop review` or PR opened)

| Step | Responsibility |
|---|---|
| init-context | Create only what later steps need: work dir, `.reviewloop/` under it |
| pre-review-comment | Post or update the summary comment to "reviewing" |
| get-diff | CI: code is checked out; fetch the target branch, compute `merge-base..head`. Server mode (later) clones |
| get-pr-info | Title, body, branches, SHAs, author, fork flag; ticket keys found with `tracker.key_pattern` |
| fetch-previous-comments | Read the bot's threads from the VCS; parse hidden markers |
| load-config | Read `.reviewloop.yml` and repo rule files from the **target branch** |
| resolve-review-context | Fetch tickets through the tracker adapter |
| detect-stack | Deterministic: match changed paths and manifests against rule front matter |
| summarize-context | Agent: synthesize business context from tickets. If none and `require_context: true`, stop with a notice |
| build-context | Write diff, context, rules and previous findings to `.reviewloop/` for the agent |
| large-diff-review | When the diff exceeds the threshold, split into groups, one agent per group, merge results |
| security-branch | Optional scanners (opengrep, gitleaks) when installed, then security-review and security-verify agents |
| initial-review | Agent: code-review skill over the diff (skipped when large-diff-review ran) |
| pr-overview | Agent: overview text for the summary |
| security-merge | Merge security findings; findings with no diff line become general threads |
| quality-gate | Agent: filter and rewrite findings (rules in 5.3) |
| post-comment | Anchor each finding (section 7), post inline or general, attach suggestions |
| summary | Update the summary comment: overview, findings by severity, file coverage, score, run-state marker |
| clean | Remove the work dir (skippable in CI) |

Security-branch and the main review branch run in parallel.

### 5.2 Follow-up (`/reviewloop recheck`)

| Step | Responsibility |
|---|---|
| in-progress-comment | Update the summary to "re-checking" |
| resolve-target-turn | Read the run-state marker: previous head SHA, turn number, open finding ids |
| force-push-and-lineage | Detect force push; pick the diff base for this turn |
| load-thread-inputs | Load open bot threads and their replies; the bot is the user of the token in use |
| build-follow-up-context | Turn each thread into a transcript plus the code now at its anchor |
| security-rescan | For secret findings, rescan history instead of trusting the reply |
| follow-up-review | Agent: decide per thread `fixed`, `not_fixed`, `valid_rebuttal`, `no_response` |
| post-follow-up-comments | Reply in each thread; resolve fixed and validly rebutted threads |
| follow-up-summary | Update the summary and the run-state marker |

A push alone triggers nothing. Fixes are re-checked only on command.

### 5.3 Quality gate rules

- Drop a finding only on ground A (neither its `existing_code` nor its `cause_code` is in the diff) or ground B (a
  diff line directly contradicts the claim, no multi-step reasoning).
- Never drop findings in protected categories: behavior or API contract change, transactions and concurrency,
  authorization, data loss, null or bounds safety.
- A vague finding is rewritten to name the code and the fix, not dropped.
- The agent writes `analysis` before the list of dropped ids.
- Duplicates of a previous finding (same fingerprint) are dropped deterministically in Python, not by the agent.

## 6. Triggers, state and config

### 6.1 Triggers and commands

| Event | GitHub | GitLab |
|---|---|---|
| PR opened | `pull_request: opened, reopened, ready_for_review` | Job runs on every `merge_request_event` pipeline and exits within seconds when the MR already has a reviewloop summary comment |
| Command | `issue_comment` | "Comments" webhook calling the pipeline trigger API; job reads the MR number from `TRIGGER_PAYLOAD` |

- Commands are fixed: `/reviewloop review`, `/reviewloop recheck`, `/reviewloop help`. Anything else exits 0
  immediately without calling an agent.
- Commands are accepted only from users with write access (GitHub `author_association` OWNER, MEMBER or
  COLLABORATOR; GitLab access level Developer or higher, checked through the API).
- Fork PRs are skipped unless a user with write access runs a command. Fork code is read as data: the job checks
  out the trusted base and fetches the PR head objects only.
- Overlapping runs on one PR:
  - GitHub: `concurrency` keyed by PR number with `cancel-in-progress`.
  - GitLab: no auto-cancel, because command pipelines share one ref and would cancel reviews of other MRs. Each run
    reads the `turn` from the state marker at start and re-reads it before posting; if another run advanced it, the
    later run stops without posting.
- GitLab docs explain how to enable merge request pipelines when a project's `workflow:rules` disable them.

### 6.2 State

There is no database. State lives in hidden HTML comments on the PR:

```
<!-- reviewloop:finding {"v":1,"id":"f-3a9c","fp":"9e1b…","sev":"critical","rule":"LAR-API-1"} -->
<!-- reviewloop:state {"v":1,"sha":"d1d5852","base":"8c01e2a","turn":2,"open":["f-3a9c","f-77b0"]} -->
```

- Each finding thread carries a `finding` marker. The summary comment carries the `state` marker and is edited in
  place.
- Markers are untrusted input: parsed with a pydantic schema; an invalid or missing state marker means "no previous
  run" and a full review runs.
- The fingerprint `fp` is derived from path, rule id and normalized quoted code, so a finding survives line shifts.

### 6.3 Config: `.reviewloop.yml`

```yaml
version: 1
agent: claude-code            # claude-code | codex | cursor
model: sonnet                 # passed to the CLI as is
language: en                  # language of review comments
paths:
  exclude: ["**/*.lock", "dist/**"]
rules:
  - path: "app/Http/**"
    file: .reviewloop/rules/api.md
tracker:
  type: jira                  # github | gitlab | jira
  url: https://jira.example.com
  key_pattern: "[A-Z]+-\\d+"
  require_context: false
security:
  enabled: true
  scanners: [opengrep, gitleaks]
suggestions: true
fail_on: none                 # none | critical | high
```

- Read from the target branch only. Per-repository only; no `extends`.
- Secrets are never read from this file.
- Defaults apply when the file is missing; reviewloop works with zero config.

### 6.4 CI result

- Exit codes: 0 success, 1 findings at or above `fail_on`, 2 infrastructure error (agent failed, timeout, VCS
  error). Default `fail_on: none`.
- Artifacts: `reviewloop.json` and `reviewloop.sarif`.
- File coverage: every file in the diff ends as `reviewed`, `failed` (with reason) or `excluded` (with reason).
  - `excluded` by default: binary, lockfiles, generated or vendored paths (built-in list plus `paths.exclude`),
    deleted files, rename-only and mode-only changes.
  - `reviewed`: the agent step that received the file succeeded **and** its output lists the file in `coverage`
    (the output contract requires one entry per input file). A missing entry gets one repair turn; still missing
    means `failed`.
  - A file too large even for one large-diff group is `failed` with reason `too_large`.
  - Any `failed` file is listed in the summary and the job exits 2.

## 7. Anchoring and suggestions

Each finding from an agent carries `path`, `line`, `side` and `existing_code`, a verbatim quote of the code it is
about. A finding about unchanged code broken by the change (for example a caller of a function whose signature
changed) also carries `cause_code`, a verbatim quote of the changed code that causes it. Such a finding is anchored
on the `cause_code` line and its text names the affected location. Python resolves the position, in order:

1. Exact match of `existing_code` in the new side of the file's hunks, nearest to `line`.
2. Same, ignoring whitespace differences.
3. The old side (removed lines).
4. The full new file, then post as a file-level comment when the match is outside the diff.
5. Another changed file, only on a single unique match.
6. Fall back to `line` when it is a changed line; otherwise the finding goes back to the agent in a repair turn.

Suggestions: when `suggestions: true`, a finding may include `suggestion` (replacement code). It is rendered in the
platform's native syntax only when the anchor is on the new side, the replaced range is at most 30 lines, and the
finding is not security. The fence length is chosen so replacement code containing backticks cannot break it.
Platforms without native suggestions show no block.

## 8. Adapters

### 8.1 VCS

```python
class VcsAdapter(Protocol):
    def event(self) -> Event
    def pull_request(self) -> PullRequest
    def bot_identity(self) -> User
    def can_write(self, user: User) -> bool
    def threads(self) -> list[Thread]
    def post_inline(self, finding: Finding, anchor: Anchor) -> ThreadRef
    def post_general(self, body: str) -> ThreadRef
    def reply(self, thread: ThreadRef, body: str) -> None
    def resolve(self, thread: ThreadRef) -> None
    def upsert_summary(self, body: str) -> None
    def suggestion_block(self, anchor: Anchor, code: str) -> str | None
```

- Milestone 1: GitHub (PyGithub) and GitLab (python-gitlab). Official SDKs first; `httpx` only where an SDK lacks
  the call.
- GitHub posts with `GITHUB_TOKEN` (`pull-requests: write`). GitLab needs a project access token in
  `REVIEWLOOP_GITLAB_TOKEN`, because `CI_JOB_TOKEN` cannot post notes.

### 8.2 Agents

```python
class AgentAdapter(Protocol):
    name: str
    def check_auth(self) -> None
    def run(self, task: AgentTask) -> AgentResult
```

- Milestone 1: Claude Code, Codex, Cursor.
- Tools: read tools plus a shell allowlist of `git log`, `git blame`, `git show`, `git diff`. No test commands in
  milestone 1. Each adapter maps this to its CLI's own permission mechanism.
- Hard isolation, enforced by reviewloop, not left to the CLI:
  - The agent works in a separate copy of the checkout with no remotes, no `.git/config` credentials and no
    credential helpers. VCS and tracker tokens never exist inside it.
  - The agent process gets a scrubbed environment: its own model credential, `PATH` and `HOME` only.
  - After each agent run, reviewloop verifies that nothing changed in the copy except `.reviewloop/out/<step>.json`.
    Any other change fails the step.
  - An agent CLI that cannot be restricted to this tool set moves to milestone 1.5 instead of shipping weaker.
- Output: the agent writes `.reviewloop/out/<step>.json`. Python validates it with pydantic; on failure the
  validation errors go back to the agent, at most 2 repair turns, then the step fails.
- Credentials: `ANTHROPIC_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN`, Bedrock and Vertex variables, `OPENAI_API_KEY`,
  Codex `auth.json` (docs warn that its refresh token rotates and must be re-saved), `CURSOR_API_KEY`. Docs
  recommend API keys or OIDC for teams and state that a personal subscription token must not be shared by a team.
- Isolation: in CI the runner container plus the tool allowlist. Bubblewrap is milestone 2 (server).

### 8.3 Trackers

GitHub Issues and GitLab Issues reuse the VCS token. Jira (Cloud and Data Center) uses `REVIEWLOOP_JIRA_TOKEN`.

## 9. Skills and rules

Skills, rewritten in English from public sources; the old skills are only a list of topics:

| Skill | Purpose |
|---|---|
| output-contract | Output schema, severities (critical, high, medium, low), `existing_code`, suggestions, comment language |
| code-review | Review method; security section toggled by config |
| quality-gate | Rules in 5.3 |
| follow-up | Thread verdicts in 5.2 |
| security-review, security-verify | Security findings, then independent verification |
| summarize-context | Business context from tickets |
| pr-overview | Overview text |

Repository rule files from config are added on top of the built-in stack rules; on a conflict the repository
rule wins. Repository rules get ids prefixed `REPO-` so they never collide with built-in ids in markers.

Rules: `rules/<stack>.md`, one per stack, front matter `stack`, `applies_to`, `detect`. One line per rule:
`ID [severity] problem, why, fix.` A Bad/Good example of at most two lines only when the rule is unreadable without
it. Milestone 1 stacks: TypeScript, React, Node/NestJS, Python, Django, Java, Go, PHP, Laravel, C#, plus a common
file for bugs, performance and security.

Length limits enforced in CI: about 600 words per rule file, about 800 words per `SKILL.md`.

Eval: about 20 seeded PRs plus about 20 AACR-Bench PRs (Apache-2.0; downloaded by the eval script at run time,
never committed). A finding matches ground truth deterministically: same file, line within ±3 of the expected line,
and same category. Precision and recall are computed from those matches; no LLM judge. Runs in CI when `skills/`
or `rules/` change, on maintainer branches only (it needs an agent credential). The first run sets the baseline;
later runs fail when either metric drops more than a set margin below it. The gate for the first release is:
baseline recorded, and the seeded set reaches recall of at least 0.6 at precision of at least 0.5.

## 10. Repository and release

```
reviewloop/
├── action.yml                 GitHub Action (Marketplace needs it at the root)
├── templates/reviewloop.yml   GitLab CI component (catalog needs templates/ at the root)
├── src/reviewloop/
│   ├── cli.py  anchor.py  py.typed
│   ├── pipeline/  state/  config/  vcs/  agents/  trackers/  security/  output/
│   ├── skills/  rules/
├── tests/  unit/  integration/  fixtures/  e2e/
├── eval/
├── examples/
├── docs/  (MkDocs Material)  adr/  specs/
├── .github/  workflows/  ISSUE_TEMPLATE/  PULL_REQUEST_TEMPLATE.md  CODEOWNERS  dependabot.yml
├── README.md  LICENSE  CLA.md  CONTRIBUTING.md  CODE_OF_CONDUCT.md  SECURITY.md  GOVERNANCE.md
├── CHANGELOG.md  AGENTS.md  mkdocs.yml  pyproject.toml  uv.lock  Dockerfile
├── .pre-commit-config.yaml  .editorconfig  .gitignore  .gitattributes
└── .reviewloop.yml            reviewloop reviews its own PRs
```

- Tooling: uv, hatchling, ruff, pyright strict, pytest on Python 3.11, 3.12 and 3.13, pre-commit, Typer.
- From the first commit: protected `main` (PR only, one approval, green CI), Conventional Commits check, squash
  merge, coverage at least 85%, Dependabot, CodeQL, OpenSSF Scorecard, actions pinned by SHA.
- Release: release-please opens the release PR from Conventional Commits; merging it tags `vX.Y.Z`, publishes to
  PyPI through Trusted Publishing, pushes `ghcr.io/tpham1467/reviewloop` signed with cosign with SBOM and SLSA
  provenance, and moves the `v1` tag for the Action. SemVer, `0.x` until the config schema is stable.
- Image: Python, git, opengrep, gitleaks and the three agent CLIs at pinned versions. Open risk: the
  redistribution terms of the Claude Code and Cursor binaries are unverified and must be checked before the first
  public image; the fallback is an image without CLIs plus a documented `Dockerfile` for users to build their own.
- Hosting: GitHub `tpham1467/reviewloop` is primary; a GitLab.com mirror publishes the CI component.
- CLA through CLA Assistant, text based on the Apache ICLA.
- The repository is public from the first commit. Every commit must be clean (no company content, no secrets);
  gitleaks runs in pre-commit and CI. The first release (`v0.1.0`) is tagged when milestone 1 works on GitLab and
  GitHub, docs are complete and the eval meets its gate.

## 11. Left to the builder

- Internal module layout under `pipeline/`, the `RunContext` shape, and step registration.
- Large-diff thresholds and group parallelism (start from: split above about 3,000 changed lines, at most 20 groups,
  at most 5 in parallel), adjusted to runner memory.
- Exact per-agent CLI flags and permission files that implement the tool allowlist.
- Timeouts per step, scaled by diff size.
- Summary score formula, as long as a review with failed files never scores the maximum.
- Eval regression margin.
- Wording of comments, summary and help text.
