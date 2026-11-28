# GitLab

reviewloop runs as a job from a CI/CD component, in merge request pipelines and in trigger pipelines started by a
Comments webhook. It works on GitLab.com and self-managed GitLab with the Docker or Kubernetes executor.

## 1. Project access token

**Settings > Access tokens > Add new token**: role **Developer**, scope **`api`**. The token's bot user posts the
comments. `CI_JOB_TOKEN` cannot post notes, so a project access token is required.

## 2. CI/CD variables

**Settings > CI/CD > Variables**, each with **Masked** (or **Masked and hidden**) checked and **Type: Variable**:

| Key | Value |
| --- | --- |
| `REVIEWLOOP_GITLAB_TOKEN` | The project access token |
| `ANTHROPIC_API_KEY` | Your agent credential ([other options](../agents/claude-code.md)) |

!!! warning "Not File-type variables"
    Do not store tokens as **File** variables. GitLab writes them as plain files under `<builds>/<project>.tmp/`,
    next to the checkout, where they stay for the whole job. See [Security](../security.md#gitlab-file-type-variables).

Leave **Protect variable** unchecked. Merge request pipelines run on the source branch, which is usually not
protected, and protected variables are not passed to them.

## 3. Include the component

Add to `.gitlab-ci.yml`:

```yaml
include:
  - component: $CI_SERVER_FQDN/tpham1467/reviewloop/reviewloop@0
    inputs:
      agent: claude-code
```

`@0` selects the latest published `0.x.y` release from the CI/CD Catalog; pin an exact version such as `@0.1.0` to
stay on one. On a self-managed instance without access to the GitLab.com catalog, mirror the reviewloop repository
into your instance and reference an exact tag of that project (partial versions only work for published Catalog
components).

| Input | Default | Meaning |
| --- | --- | --- |
| `agent` | `claude-code` | Agent CLI. `codex` and `cursor` are experimental |
| `model` | empty | Model name passed to the CLI as is |
| `image_tag` | the component release's exact version | Tag of `ghcr.io/tpham1467/reviewloop` |
| `stage` | `test` | Stage of the `reviewloop` job |

The job runs on `merge_request_event` and `trigger` pipelines. It is not `interruptible`: command pipelines all run
on one ref, so auto-cancel would stop reviews of other merge requests. Two runs on one merge request do not collide:
each re-reads the run state before posting and the later one stops.

The image runs as uid 1001, a non-root user. The runner's checkout is owned by root, which git would refuse;
reviewloop trusts exactly that checkout path for its git calls (`safe.directory`), so no `user` override is needed.
The job writes `reviewloop.json` and `reviewloop.sarif` into the checkout, which needs write access for uid 1001 (the
default for Docker executor builds).

## 4. Enable merge request pipelines

A project that has no `workflow:rules` gets merge request pipelines from the component's rules. A project whose
`workflow:rules` only allow branch pipelines must also allow these two sources. A common setup that avoids duplicate
branch and merge request pipelines:

```yaml
workflow:
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
    - if: $CI_PIPELINE_SOURCE == "trigger"
    - if: $CI_COMMIT_BRANCH && $CI_OPEN_MERGE_REQUESTS
      when: never
    - if: $CI_COMMIT_BRANCH
```

Open a merge request. The job posts a "reviewing" summary note, then inline discussions, then the final summary. Later
merge request pipelines (every push) start the job too; it sees the summary note and exits within seconds.

## 5. Commands through a Comments webhook

GitLab CI does not start on comments, so a webhook starts a trigger pipeline.

!!! warning "Every comment starts a pipeline with all your jobs"
    The webhook fires on every comment on every merge request and issue, and each one creates a full `trigger`
    pipeline on the default branch: every job whose rules allow it runs, deploy jobs included. Before adding the
    webhook, keep your other jobs out of trigger pipelines by adding this rule first to each of them:

    ```yaml
    rules:
      - if: $CI_PIPELINE_SOURCE == "trigger"
        when: never
      # ... the job's existing rules, or `- when: on_success`
    ```

    Jobs that use `only`/`except` instead of `rules` need `except: [triggers]`.

    See [`examples/gitlab-ci.yml`](https://github.com/tpham1467/reviewloop/blob/main/examples/gitlab-ci.yml). If
    you already use trigger pipelines for something else, key those jobs on a variable your other trigger callers set
    instead.

    A marker variable in the webhook URL (`variables[REVIEWLOOP]=1`) is not used: GitLab refuses a trigger that sets
    pipeline variables unless the trigger token's owner may set them (project setting **Minimum role to use pipeline
    variables**, `no_one_allowed` by default for new GitLab.com projects).

1. **Settings > CI/CD > Pipeline trigger tokens > Add new token.** Copy the token.
2. **Settings > Webhooks > Add new webhook**:
    - **URL**:
      `https://gitlab.example.com/api/v4/projects/<project_id>/ref/<default_branch>/trigger/pipeline?token=<trigger_token>`
      (URL-encode the branch name if it contains `/`).
    - **Mask portions of URL**: mask the trigger token, so it is hidden in the webhook settings and logs.
    - **Trigger**: **Comments** only.
3. Save, then comment `/reviewloop help` on a merge request to test it.

The job reads the merge request from `TRIGGER_PAYLOAD`, checks out its target branch and fetches its head. In the
pipeline of a comment that is not a `/reviewloop` command, the reviewloop job finishes within seconds without calling
an agent. A failed fetch or checkout exits `2`, like any infrastructure error.

| Command | Effect |
| --- | --- |
| `/reviewloop review` | Review the whole merge request again |
| `/reviewloop recheck` | Re-check open findings against new commits and replies; resolve fixed threads |
| `/reviewloop help` | Post the command list |

Only project members with the **Developer** role or higher can run commands; reviewloop checks the role through the
API, not the webhook.

## Fork merge requests

A merge request pipeline from a fork runs in the fork's project, without your CI/CD variables, so no review happens.
A member with Developer access comments `/reviewloop review`: the trigger pipeline runs in your project and reads the
fork's code as data only.

## Fail the pipeline on findings

Set `fail_on: high` (or `critical`) in `.reviewloop.yml`. Exit codes: `0` success, `1` findings at or above
`fail_on`, `2` infrastructure error. Add `allow_failure: true` to the job (in your `.gitlab-ci.yml`, by redefining
`reviewloop:` keys) if the merge request should stay mergeable anyway.

## Without the component

If you cannot include components, copy the job from
[`templates/reviewloop.yml`](https://github.com/tpham1467/reviewloop/blob/main/templates/reviewloop.yml) and replace
the `$[[ inputs.* ]]` placeholders.
