# Claude Code

Claude Code is the supported agent in milestone 1 and the default (`agent: claude-code`). The image ships
`@anthropic-ai/claude-code` at an exact version.

## Credentials

Set one of these as a CI secret (GitHub) or masked variable (GitLab):

| Variable | Use |
| --- | --- |
| `ANTHROPIC_API_KEY` | Anthropic API key. Recommended for teams |
| `CLAUDE_CODE_OAUTH_TOKEN` | Token from `claude setup-token` for a personal subscription. One person's own repositories only |
| `CLAUDE_CODE_USE_BEDROCK=1` | Amazon Bedrock, with `AWS_REGION` and `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_SESSION_TOKEN` or `AWS_BEARER_TOKEN_BEDROCK` |
| `CLAUDE_CODE_USE_VERTEX=1` | Google Vertex AI, with `CLOUD_ML_REGION`, `ANTHROPIC_VERTEX_PROJECT_ID` and `GOOGLE_APPLICATION_CREDENTIALS` |

Other `ANTHROPIC_*` variables (for example `ANTHROPIC_BASE_URL`) pass through to the CLI as well.

Each agent run gets a fresh, empty `HOME`, so file-based logins do not work: no `~/.claude` login, no AWS profile and no
Google application-default credentials under `HOME`. For Vertex, `GOOGLE_APPLICATION_CREDENTIALS` must point at a key
file outside `HOME`.

### OIDC instead of long-lived keys

On GitHub, Bedrock or Vertex can use short-lived credentials from the job's OIDC token, so no cloud key is stored:

```yaml
permissions:
  contents: read
  pull-requests: write
  id-token: write
steps:
  # ... checkout and head fetch as in the example workflow ...
  - uses: aws-actions/configure-aws-credentials@e7f100cf4c008499ea8adda475de1042d6975c7b # v6.2.0
    with:
      role-to-assume: arn:aws:iam::123456789012:role/reviewloop-bedrock
      aws-region: us-east-1
  - uses: tpham1467/reviewloop@v0
    env:
      CLAUDE_CODE_USE_BEDROCK: "1"
```

The credentials action exports `AWS_*` variables to later steps, and the action passes them to the container by name.
GitLab supports the same pattern with `id_tokens:` and your cloud's web identity federation.

!!! warning "Personal subscription tokens"
    A `CLAUDE_CODE_OAUTH_TOKEN` belongs to one person's subscription. Never store it where a team's pipelines can use
    it: everyone who can run the job spends that person's quota under their account. Teams use an API key, Bedrock,
    Vertex or OIDC.

## Model

`model:` in `.reviewloop.yml` (or the action/component `model` input) is passed to `claude --model` as is: an alias
such as `sonnet` or `opus`, or a full model name. Empty uses the CLI default.

## What the agent can do

- Tools: `Read`, `Grep`, `Glob`, `Skill`, `Write` to its one output file, and `Bash` limited to `git log`,
  `git blame`, `git show` and `git diff` through a git guard first on `PATH`.
- No web access, no MCP servers, no hooks. Settings, hooks, commands and `CLAUDE.md` from the checkout are not loaded.
- Reads and writes outside the working copy are blocked (`blockReadsOutsideWorkingDirectories`).

reviewloop also enforces its own isolation, whatever the CLI does: see [Security](../security.md).
