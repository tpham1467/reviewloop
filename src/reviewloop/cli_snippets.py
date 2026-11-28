"""CI configuration that `reviewloop init` prints for each platform.

Each snippet is the matching file under `examples/`, byte for byte (tests/unit/test_cli.py checks it): edit the
example file and paste it here. The examples are not shipped in the wheel, so the text lives in this module.
"""

from reviewloop.platform import Platform

GITHUB = r"""# Copy to .github/workflows/reviewloop.yml and add the ANTHROPIC_API_KEY repository secret.
name: reviewloop

on:
  pull_request:
    # No `synchronize`: reviewloop ignores pushes, and with cancel-in-progress each push would cancel a running review.
    types: [opened, reopened, ready_for_review]
  issue_comment:
    types: [created]

permissions:
  contents: read
  pull-requests: write

jobs:
  review:
    # Pull request events, or a `/reviewloop ...` comment on a pull request by a user with write access. The filter
    # keeps other users from cancelling a running review; reviewloop still checks the commenter's access itself.
    if: >-
      github.event_name == 'pull_request' ||
      (github.event.issue.pull_request && startsWith(github.event.comment.body, '/reviewloop') &&
       contains(fromJSON('["OWNER","MEMBER","COLLABORATOR"]'), github.event.comment.author_association))
    # Job-level concurrency is evaluated after `if`: only runs that pass the filter cancel a running review.
    concurrency:
      group: reviewloop-${{ github.event.pull_request.number || github.event.issue.number }}
      cancel-in-progress: true
    runs-on: ubuntu-latest
    timeout-minutes: 60
    steps:
      # Check out the trusted base branch (all branches, full history). The PR head is fetched as git objects only and
      # never checked out, and no token is stored in .git/config.
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
      - uses: actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9 # v7.0.2
        if: always()
        with:
          name: reviewloop
          path: |
            reviewloop.json
            reviewloop.sarif
          if-no-files-found: ignore
"""

GITLAB = r"""# Add to .gitlab-ci.yml. Set these masked CI/CD variables (type Variable, not File, and not protected):
#   REVIEWLOOP_GITLAB_TOKEN  project access token, role Developer, scope api
#   ANTHROPIC_API_KEY        your agent credential
# For /reviewloop commands, add a Comments webhook that calls the pipeline trigger API (docs/getting-started/gitlab.md).
include:
  - component: $CI_SERVER_FQDN/tpham1467/reviewloop/reviewloop@0
    inputs:
      agent: claude-code

# Allow merge request and trigger pipelines, without duplicate branch pipelines for open merge requests.
workflow:
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
    - if: $CI_PIPELINE_SOURCE == "trigger"
    - if: $CI_COMMIT_BRANCH && $CI_OPEN_MERGE_REQUESTS
      when: never
    - if: $CI_COMMIT_BRANCH

# Every merge request comment starts a trigger pipeline through the webhook. Keep your other jobs out of it, or each
# comment runs them, deploys included. Put this rule first in every job other than reviewloop:
#
# build:
#   rules:
#     - if: $CI_PIPELINE_SOURCE == "trigger"
#       when: never
#     - when: on_success
"""

BITBUCKET_CLOUD = r"""# Bitbucket Cloud: review every pull request, plus a manual pipeline for commands.
# Set REVIEWLOOP_BITBUCKET_TOKEN (access token) and your agent credential as
# secured repository variables.
# Exact version, bumped on each release; review a new version before you update it.
image: ghcr.io/tpham1467/reviewloop:0.1.0 # x-release-please-version

pipelines:
  pull-requests:
    '**':
      - step:
          name: reviewloop
          script:
            - reviewloop run
  custom:
    reviewloop-command:
      - variables:
          - name: PR_ID
          - name: COMMAND
            default: recheck
            allowed-values: [review, recheck, help]
      - step:
          name: reviewloop command
          script:
            - reviewloop run
"""

BITBUCKET_DC = r"""// Reviews Bitbucket Data Center pull requests with reviewloop.
// Requires the Jenkins "Generic Webhook Trigger" (1.68 or later, for tokenCredentialId) and "Docker Pipeline"
// plugins.
//
// Bitbucket webhook (events: PR opened, PR comment added) ->
//   https://jenkins.example.com/generic-webhook-trigger/invoke?token=<value of reviewloop-webhook-token>
pipeline {
  agent any

  triggers {
    GenericTrigger(
      genericVariables: [
        [key: 'BB_PROJECT', value: '$.pullRequest.toRef.repository.project.key'],
        [key: 'BB_REPO',    value: '$.pullRequest.toRef.repository.slug'],
        [key: 'BB_PR_ID',   value: '$.pullRequest.id'],
        [key: 'BB_EVENT',   value: '$.eventKey'],
        [key: 'BB_COMMENT', value: '$.comment.text', defaultValue: ''],
        [key: 'BB_ACTOR',   value: '$.actor.slug'],
        [key: 'BB_TARGET',  value: '$.pullRequest.toRef.displayId'],
      ],
      tokenCredentialId: 'reviewloop-webhook-token',
      causeString: 'Bitbucket $BB_EVENT on $BB_PROJECT/$BB_REPO#$BB_PR_ID',
      regexpFilterText: '$BB_EVENT:$BB_PR_ID',
      regexpFilterExpression: '^pr:(opened|comment:added):[0-9]+$',
      printContributedVariables: false,
      printPostContent: false,
    )
  }

  environment {
    BB_URL = 'https://bitbucket.example.com'
  }

  stages {
    stage('Checkout target branch') {
      steps {
        checkout([
          $class: 'GitSCM',
          branches: [[name: "*/${env.BB_TARGET}"]],
          userRemoteConfigs: [[
            url: "https://bitbucket.example.com/scm/${env.BB_PROJECT.toLowerCase()}/${env.BB_REPO}.git",
            credentialsId: 'reviewloop-bitbucket-git',
          ]],
        ])
      }
    }

    stage('Review') {
      agent {
        docker {
          // Exact version, bumped on each release; review a new version before you update it.
          image 'ghcr.io/tpham1467/reviewloop:0.1.0' // x-release-please-version
          args '--entrypoint='
          reuseNode true
        }
      }
      steps {
        withCredentials([
          string(credentialsId: 'reviewloop-bitbucket-token', variable: 'REVIEWLOOP_BITBUCKET_TOKEN'),
          string(credentialsId: 'reviewloop-agent-api-key', variable: 'ANTHROPIC_API_KEY'),
        ]) {
          sh 'reviewloop run'
        }
      }
    }
  }
}
"""

SNIPPETS = {
    Platform.GITHUB: GITHUB,
    Platform.GITLAB: GITLAB,
    Platform.BITBUCKET_CLOUD: BITBUCKET_CLOUD,
    Platform.BITBUCKET_DC: BITBUCKET_DC,
}
