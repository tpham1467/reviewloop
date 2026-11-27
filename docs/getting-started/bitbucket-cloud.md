# Bitbucket Cloud

## Setup

1. Create a repository (or workspace) access token with the scopes `pullrequest:write` and `repository:read`. Add `repository:admin` only if you run commands (see below).
2. Add repository variables, both marked as secured:
   - `REVIEWLOOP_BITBUCKET_TOKEN`: the access token.
   - Your agent credential (see the agent docs for the variable name).
3. Copy [`examples/bitbucket-pipelines.yml`](../../examples/bitbucket-pipelines.yml) to
   `bitbucket-pipelines.yml` in the repository root.
4. Enable Pipelines for the repository.

Every pull request now runs the `pull-requests` pipeline and posts a review. A pull request
that already has the bot's summary comment is skipped.

## Bot account UUID

The adapter must know which account is the bot to find its own summary and threads. It asks
`GET /2.0/user`, which repository and workspace access tokens may be denied (it can need the
`account` scope). When that happens the run fails with an error naming
`REVIEWLOOP_BITBUCKET_BOT_UUID`. Set it as a repository variable to the token's bot account UUID:

1. Post one comment on any pull request with the token (for example with `curl` and the
   token as a Bearer header).
2. Read that comment from the API (`GET /2.0/repositories/{workspace}/{repo}/pullrequests/{id}/comments`)
   and copy `user.uuid`, braces included, for example `{...}`.

Skip this step if `/2.0/user` works for your token.

## Run a command

Bitbucket pipelines do not start on comments, so commands run from the UI:

1. Open **Pipelines** and choose **Run pipeline**.
2. Select the branch, then the **custom** pipeline `reviewloop-command`.
3. Set `PR_ID` to the pull request number and `COMMAND` to `review`, `recheck` or `help`.
4. Run it. Only users with write or admin access to the repository can trigger commands.

Checking the triggering user's permission reads the repository's user permissions, which Bitbucket
restricts to the `repository:admin` scope. Without it the command fails with an error naming the
missing scope; the user is never silently rejected. Review runs on pull requests do not need it.

Only explicit user permissions on the repository are checked. A user who has write access only
through a group or the workspace must be granted an explicit user permission before they can
run commands.

## Limits

- Bitbucket Cloud has no suggestion blocks; findings are posted as plain comments.
- Inline comments anchor on a single line; multi-line ranges are not supported.
