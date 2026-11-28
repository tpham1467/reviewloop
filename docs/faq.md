# FAQ

## Does any code or key leave my infrastructure?

Only through your agent CLI, to the model provider you configured. reviewloop has no server and no telemetry.

## Why does a push not trigger a review?

By design: a review runs when a pull request opens (or becomes ready), and on `/reviewloop review`. After fixing,
comment `/reviewloop recheck`: reviewloop re-checks each open finding against the new code and your replies, and
resolves the fixed threads.

## The job exits with code 2. What failed?

Code `2` is an infrastructure error: the agent failed or timed out, the VCS API failed, or a file could not be
reviewed. The summary comment names the failed step or files, and the job log has the details (secrets redacted). Set
`REVIEWLOOP_KEEP_WORKDIR=1` to keep the agent's inputs and transcripts in the work directory: `$RUNNER_TEMP/reviewloop-*`
with the GitHub Action (upload it as an artifact to read it), `$CI_BUILDS_DIR/reviewloop-*` on GitLab.

## "get-diff: git merge-base failed"

The job has no history of the target branch or the pull request head. Use the example workflow or the component:
they check out the target branch with full history and fetch the head. On GitLab, do not set `GIT_DEPTH` lower in
your own variables.

## "detected dubious ownership in repository"

git refuses a repository owned by another user. reviewloop does not need to own the checkout: it runs its git commands
with global and system config disabled and trusts exactly the CI checkout path through `safe.directory`, including the
clone that builds the agent sandbox. It never trusts `*` or the sandbox. The image runs as uid 1001, so a root-owned
GitLab checkout works as is.

If you still see this error, it comes from a git command in your own job script, not from reviewloop: run it with
`git -c safe.directory="$PWD" ...`. The job also needs write access to the working directory for `reviewloop.json` and
`reviewloop.sarif`.

## My GitLab merge request has no reviewloop job

Merge request pipelines are not enabled: see [Enable merge request pipelines](getting-started/gitlab.md#4-enable-merge-request-pipelines).
A fork's merge request pipeline runs in the fork, without your variables; comment `/reviewloop review` instead.

## Commands on GitLab do nothing

Check the webhook under **Settings > Webhooks > Edit > Recent events**: it must use the **Comments** trigger and return
`201`. The commenter needs the Developer role or higher.

## Can I use Codex or Cursor?

Yes, as experimental agents: set `REVIEWLOOP_EXPERIMENTAL_AGENTS=1`. Neither can be held to the review tool set yet
(see [ADR 0002](adr/0002-agent-tool-restriction.md)). The image does not include Cursor; see
[Cursor](agents/cursor.md) for a derived image.

## How much does a review cost?

Your model provider bills the agent's usage. A typical pull request runs several agent steps (review, overview,
quality gate and, when enabled, two security steps). Large diffs are split into groups, one agent run each.

## Where are the results besides comments?

`reviewloop.json` (findings and per-file coverage) and `reviewloop.sarif` in the job's working directory. The examples
upload them as artifacts.
