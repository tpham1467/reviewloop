import re
from collections.abc import Mapping

from reviewloop.trackers.base import TrackerAdapter
from reviewloop.trackers.github_issues import GithubIssuesTracker
from reviewloop.trackers.gitlab_issues import GitlabIssuesTracker
from reviewloop.trackers.jira import JiraTracker, is_cloud


def extract_keys(text: str, pattern: str) -> list[str]:
    """Return unique ticket keys in order of appearance."""
    seen: dict[str, None] = {}
    for match in re.finditer(pattern, text):
        seen.setdefault(match.group(0), None)
    return list(seen)


def get_tracker(kind: str | None, url: str | None, env: Mapping[str, str]) -> TrackerAdapter | None:
    """Build the configured tracker, or None when unconfigured or credentials are missing."""
    if kind == "github":
        token, repo = env.get("GITHUB_TOKEN"), env.get("GITHUB_REPOSITORY")
        if token and repo:
            return GithubIssuesTracker.from_token(token, repo)
    elif kind == "gitlab":
        server, project = env.get("CI_SERVER_URL"), env.get("CI_PROJECT_ID")
        token = env.get("REVIEWLOOP_GITLAB_TOKEN")
        if server and project and token:
            return GitlabIssuesTracker.from_token(server, project, token)
    elif kind == "jira":
        token = env.get("REVIEWLOOP_JIRA_TOKEN")
        if url and token:
            if not is_cloud(url):
                return JiraTracker.from_credentials(url, token, None)
            email = env.get("REVIEWLOOP_JIRA_EMAIL")
            if email:
                return JiraTracker.from_credentials(url, token, email)
    return None
