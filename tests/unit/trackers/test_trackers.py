from types import SimpleNamespace
from typing import Any

import pytest
from github import GithubException
from gitlab.exceptions import GitlabGetError
from requests import HTTPError, Response

from reviewloop.errors import ReviewloopError
from reviewloop.trackers.github_issues import GithubIssuesTracker
from reviewloop.trackers.gitlab_issues import GitlabIssuesTracker
from reviewloop.trackers.jira import JiraTracker
from reviewloop.trackers.registry import extract_keys, get_tracker


def test_extract_keys_dedup_order() -> None:
    assert extract_keys("fix #3, #1 and #3 again, ABC-9", r"#(\d+)") == ["#3", "#1"]
    assert extract_keys("PROJ-2 PROJ-1 PROJ-2", r"[A-Z]+-\d+") == ["PROJ-2", "PROJ-1"]


class _Repo:
    def __init__(self) -> None:
        self.calls: list[int] = []

    def get_issue(self, number: int) -> Any:
        self.calls.append(number)
        if number == 404:
            raise GithubException(404, {}, {})
        if number == 500:
            raise GithubException(500, {}, {})
        is_pr = number == 7
        return SimpleNamespace(
            title=f"t{number}",
            body="x" * 30_000,
            html_url=f"https://gh/{number}",
            pull_request=object() if is_pr else None,
        )


def test_github_issue_keys_hash_number() -> None:
    repo = _Repo()
    tickets = GithubIssuesTracker(repo).fetch(["#12", "13", "#7", "#404", "junk"])
    assert repo.calls == [12, 13, 7, 404]
    assert [t.key for t in tickets] == ["#12", "#13"]
    assert len(tickets[0].body) == 20_000


def test_github_other_error_raises() -> None:
    with pytest.raises(ReviewloopError):
        GithubIssuesTracker(_Repo()).fetch(["#500"])


class _GlIssues:
    def get(self, number: int) -> Any:
        if number == 404:
            raise GitlabGetError("nf", 404)
        if number == 500:
            raise GitlabGetError("boom", 500)
        return SimpleNamespace(title="t", description=None, web_url=f"https://gl/{number}")


def test_gitlab_fetch_skips_missing_and_raises_other() -> None:
    tracker = GitlabIssuesTracker(SimpleNamespace(issues=_GlIssues()))
    tickets = tracker.fetch(["#1", "#404"])
    assert [(t.key, t.body) for t in tickets] == [("#1", "")]
    with pytest.raises(ReviewloopError):
        tracker.fetch(["#500"])


def _http_error(status: int) -> HTTPError:
    resp = Response()
    resp.status_code = status
    return HTTPError(response=resp)


class _JiraClient:
    def get_issue(self, key: str) -> dict[str, Any]:
        if key == "P-404":
            raise _http_error(404)
        if key == "P-500":
            raise _http_error(500)
        if key == "P-2":
            adf = {
                "type": "doc",
                "content": [
                    {"type": "paragraph", "content": [{"type": "text", "text": "Hello"}]},
                    {"type": "paragraph", "content": [{"type": "text", "text": "World"}]},
                ],
            }
            return {"fields": {"summary": "s2", "description": adf}}
        return {"fields": {"summary": "s1", "description": "wiki *text*"}}


def test_jira_fetch_skips_missing_key() -> None:
    tracker = JiraTracker(_JiraClient(), "https://jira.example.com/")
    tickets = tracker.fetch(["P-1", "P-404", "P-2"])
    assert [t.key for t in tickets] == ["P-1", "P-2"]
    assert tickets[0].body == "wiki *text*"
    assert tickets[1].body == "Hello\nWorld"
    assert tickets[0].url == "https://jira.example.com/browse/P-1"
    with pytest.raises(ReviewloopError):
        tracker.fetch(["P-500"])


def test_tracker_none_when_unconfigured() -> None:
    assert get_tracker(None, None, {}) is None
    assert get_tracker("github", None, {}) is None
    assert get_tracker("gitlab", None, {"CI_SERVER_URL": "https://gl"}) is None
    assert get_tracker("jira", "https://x.atlassian.net", {"REVIEWLOOP_JIRA_TOKEN": "FAKE"}) is None
    assert get_tracker("jira", None, {"REVIEWLOOP_JIRA_TOKEN": "FAKE"}) is None


def test_get_tracker_builds_each_kind() -> None:
    gh = get_tracker("github", None, {"GITHUB_TOKEN": "ghp_FAKE", "GITHUB_REPOSITORY": "o/r"})
    assert isinstance(gh, GithubIssuesTracker)
    gl = get_tracker(
        "gitlab",
        None,
        {"CI_SERVER_URL": "https://gl.example.com", "CI_PROJECT_ID": "5", "REVIEWLOOP_GITLAB_TOKEN": "FAKE"},
    )
    assert isinstance(gl, GitlabIssuesTracker)
    dc = get_tracker("jira", "https://jira.example.com", {"REVIEWLOOP_JIRA_TOKEN": "FAKE"})
    assert isinstance(dc, JiraTracker)
    cloud = get_tracker(
        "jira", "https://x.atlassian.net", {"REVIEWLOOP_JIRA_TOKEN": "FAKE", "REVIEWLOOP_JIRA_EMAIL": "a@example.com"}
    )
    assert isinstance(cloud, JiraTracker)
