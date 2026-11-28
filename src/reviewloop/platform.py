"""Pick the VCS adapter for the CI system the job runs in."""

import json
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path

from reviewloop.errors import ReviewloopError
from reviewloop.vcs.base import VcsAdapter
from reviewloop.vcs.bitbucket_cloud import BitbucketCloudAdapter
from reviewloop.vcs.bitbucket_dc import BitbucketDCAdapter
from reviewloop.vcs.github import GitHubAdapter
from reviewloop.vcs.gitlab import GitLabAdapter

SUPPORTED = (
    "GitHub Actions (GITHUB_ACTIONS=true), GitLab CI (GITLAB_CI=true), Bitbucket Pipelines (BITBUCKET_BUILD_NUMBER) "
    "or Bitbucket Data Center through Jenkins (JENKINS_URL and BB_PR_ID)"
)


class Platform(StrEnum):
    GITHUB = "github"
    GITLAB = "gitlab"
    BITBUCKET_CLOUD = "bitbucket-cloud"
    BITBUCKET_DC = "bitbucket-dc"


def platform_name(env: Mapping[str, str]) -> Platform | None:
    if env.get("GITHUB_ACTIONS") == "true":
        return Platform.GITHUB
    if env.get("GITLAB_CI") == "true":
        return Platform.GITLAB
    if env.get("BITBUCKET_BUILD_NUMBER"):
        return Platform.BITBUCKET_CLOUD
    if env.get("JENKINS_URL") and env.get("BB_PR_ID"):
        return Platform.BITBUCKET_DC
    return None


def detect_platform(env: Mapping[str, str]) -> VcsAdapter:
    name = platform_name(env)
    try:
        if name is Platform.GITHUB:
            return GitHubAdapter(
                env["GITHUB_TOKEN"], env["GITHUB_REPOSITORY"], Path(env["GITHUB_EVENT_PATH"]), env["GITHUB_EVENT_NAME"]
            )
        if name is Platform.GITLAB:
            return GitLabAdapter.from_env(env)
        if name is Platform.BITBUCKET_CLOUD:
            return BitbucketCloudAdapter.from_env(env)
        if name is Platform.BITBUCKET_DC:
            return BitbucketDCAdapter.from_env(env)
    except KeyError as exc:
        raise ReviewloopError(f"missing environment variable {exc.args[0]} for {name}") from None
    raise ReviewloopError(f"no supported CI system detected; reviewloop runs in {SUPPORTED}")


def for_pull_request(env: Mapping[str, str], number: int, workdir: Path) -> dict[str, str]:
    """`env` changed so the platform's adapter works on pull request `number` (a manual `review`/`recheck --pr`)."""
    out = dict(env)
    name = platform_name(env)
    if name is Platform.GITHUB:
        # The GitHub adapter reads the PR number from the event payload; give it an "opened" event for that PR.
        event = workdir / "event.json"
        event.write_text(json.dumps({"action": "opened", "number": number, "pull_request": {"number": number}}))
        out.update(GITHUB_EVENT_PATH=str(event), GITHUB_EVENT_NAME="pull_request")
    elif name is Platform.GITLAB:
        out["CI_MERGE_REQUEST_IID"] = str(number)
    elif name is Platform.BITBUCKET_CLOUD:
        out["PR_ID"] = str(number)
    elif env.get("JENKINS_URL"):
        out["BB_PR_ID"] = str(number)
    return out
