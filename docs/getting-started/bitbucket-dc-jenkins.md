# Bitbucket Data Center with Jenkins

reviewloop reviews pull requests on Bitbucket Data Center from a Jenkins job.
Minimum supported Bitbucket Data Center version: **8.0** (comment thread
resolution through `threadResolved`). Older servers reject the resolve call and
reviewloop fails it with "threadResolved requires Bitbucket Data Center 8.0 or later".

## 1. HTTP access token

Create a dedicated bot user and, as that user, create an HTTP access token
(Profile picture > Manage account > HTTP access tokens) with **project write**
or **repository write** permission. The bot needs write access to post and edit
comments. Store the token in Jenkins as a secret text credential with ID
`reviewloop-bitbucket-token`. It is passed as `REVIEWLOOP_BITBUCKET_TOKEN` and
is never logged.

## 2. Jenkins plugins and credentials

Install the **Generic Webhook Trigger** and **Docker Pipeline** plugins. Create these
credentials:

- `reviewloop-webhook-token` (Secret text): the trigger token. Put the same
  value in the webhook URL `?token=`.
- `reviewloop-bitbucket-git` (Username with password): used only for the
  checkout. Username is the bot user or `x-token-auth`, password is the HTTP
  access token.
- `reviewloop-agent-api-key` (Secret text): your agent API key.

`reviewloop-bitbucket-token` (step 1) stays a Secret text credential for the
API calls, bound with `withCredentials`.

## 3. Job

Create a Pipeline job from `examples/Jenkinsfile`. Edit `BB_URL` and the clone URL for your server. The pipeline maps these variables
from the webhook JSON:

| Variable | Meaning |
| --- | --- |
| `BB_PROJECT` | Project key |
| `BB_REPO` | Repository slug |
| `BB_PR_ID` | Pull request ID |
| `BB_EVENT` | `pr:opened` or `pr:comment:added` |
| `BB_COMMENT` | Comment text (comment events only) |
| `BB_ACTOR` | Slug of the user who triggered the event |
| `BB_TARGET` | Target branch name; the Jenkinsfile checks it out before reviewloop runs |

## 4. Webhook

In the repository (or project) settings add a webhook to
`https://jenkins.example.com/generic-webhook-trigger/invoke?token=<value of reviewloop-webhook-token>` with
the events **Pull request: Opened** and **Pull request: Comment added**.

## Commands

Comment `/reviewloop review`, `/reviewloop recheck` or `/reviewloop help` on a
pull request. The commenter must have repository write access (checked through
the Bitbucket API, not the webhook). Only permissions granted directly to the
user, at repository or project level, count. Access granted through a group is
not detected because listing group permissions needs admin rights; grant the
user directly or use a different approver.

## Limits

- No suggestion blocks are posted.
- Inline comments that the server rejects are posted as general comments with
  the file and line in front.
- Bitbucket Data Center rejects 4-byte characters (such as emoji); reviewloop
  replaces them with U+FFFD in everything it posts.
