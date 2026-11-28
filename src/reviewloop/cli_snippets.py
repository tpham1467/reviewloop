"""CI configuration that `reviewloop init` prints for each platform."""

from reviewloop.platform import Platform

GITHUB = """\
# .github/workflows/reviewloop.yml
name: reviewloop
on:
  pull_request:
    types: [opened, reopened, ready_for_review]
  issue_comment:
    types: [created]
permissions:
  contents: read
  issues: write
  pull-requests: write
jobs:
  review:
    if: >-
      github.event_name == 'pull_request' ||
      (github.event.issue.pull_request && startsWith(github.event.comment.body, '/reviewloop'))
    # Job level, after `if`: a comment that is not a command must not cancel a running review.
    concurrency:
      group: reviewloop-${{ github.event.pull_request.number || github.event.issue.number }}
      cancel-in-progress: true
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2
        with:
          persist-credentials: false
          fetch-depth: 0
      # The PR head as objects only; the token is passed to this one command, never stored in .git/config.
      - name: Fetch the pull request head
        env:
          PR_NUMBER: ${{ github.event.pull_request.number || github.event.issue.number }}
          GH_TOKEN: ${{ github.token }}
        run: >-
          git -c "http.https://github.com/.extraheader=AUTHORIZATION: basic
          $(printf 'x-access-token:%s' "$GH_TOKEN" | base64 -w0)"
          fetch --no-tags origin "refs/pull/${PR_NUMBER}/head"
      - uses: tpham1467/reviewloop@v0
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
"""

GITLAB = """\
# .gitlab-ci.yml (needs merge request pipelines; set REVIEWLOOP_GITLAB_TOKEN and your agent
# credential as masked CI/CD variables, not File-type variables)
reviewloop:
  image:
    name: ghcr.io/tpham1467/reviewloop:0
    entrypoint: [""]
  variables:
    GIT_DEPTH: 0
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
    - if: $CI_PIPELINE_SOURCE == "trigger"
  script:
    # The MR number: CI_MERGE_REQUEST_IID in MR pipelines, the comment webhook payload in trigger pipelines.
    - >-
      MR_IID="${CI_MERGE_REQUEST_IID:-$(python3 -c 'import json, os, pathlib;
      p = os.environ.get("TRIGGER_PAYLOAD", "");
      t = pathlib.Path(p).read_text() if os.path.isfile(p) else p;
      print(int(json.loads(t)["merge_request"]["iid"]))')}"
    - git fetch --no-tags origin "${CI_MERGE_REQUEST_TARGET_BRANCH_NAME:-$CI_DEFAULT_BRANCH}"
      "refs/merge-requests/${MR_IID}/head"
    - reviewloop run
"""

BITBUCKET_CLOUD = """\
# bitbucket-pipelines.yml (set REVIEWLOOP_BITBUCKET_TOKEN and your agent credential as
# secured repository variables)
image: ghcr.io/tpham1467/reviewloop:0

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

BITBUCKET_DC = """\
// Jenkinsfile stage; the full pipeline with the Generic Webhook Trigger that sets
// BB_PROJECT, BB_REPO, BB_PR_ID, BB_EVENT, BB_COMMENT and BB_ACTOR is in examples/Jenkinsfile.
stage('Review') {
  agent {
    docker {
      image 'ghcr.io/tpham1467/reviewloop:0'
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
"""

SNIPPETS = {
    Platform.GITHUB: GITHUB,
    Platform.GITLAB: GITLAB,
    Platform.BITBUCKET_CLOUD: BITBUCKET_CLOUD,
    Platform.BITBUCKET_DC: BITBUCKET_DC,
}
