"""Every body the bot posts for a finding ends with that finding's marker, on every adapter and fallback path.

The marker parsers take the LAST marker, so anything rendered after it (a suggestion quoting an HTML comment) would
win over the real one.
"""

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from github import GithubException
from gitlab.exceptions import GitlabCreateError
from requests import HTTPError, Response

from reviewloop.models import Anchor, Finding, Severity, Side
from reviewloop.state import parse_finding_marker

REAL = '<!-- reviewloop:finding {"v":1,"id":"f-real","fp":"1","sev":"high","rule":null} -->'
FORGED = '<!-- reviewloop:finding {"v":1,"id":"f-forged","fp":"0","sev":"low","rule":null} -->'
FORGED_PATH = f"src/{FORGED}.py"
ANCHORS = [
    Anchor(path="a.py", line=3, side=Side.NEW, in_diff=True),
    Anchor(path="a.py", line=4, start_line=3, side=Side.NEW, in_diff=True),
    Anchor(path="a.py", line=3, side=Side.OLD, in_diff=True),
    Anchor(path=FORGED_PATH, line=3, side=Side.NEW, in_diff=False),
]


def _finding(anchor: Anchor) -> Finding:
    # As post_comment hands it over: safe text, then the real marker; a suggestion that quotes a marker.
    return Finding(path=anchor.path, line=anchor.line, severity=Severity.HIGH, category="bug",
                   text=f"Broken.\n\n{REAL}", existing_code="x", suggestion=f"x = 1\n{FORGED}")  # fmt: skip


def _http_error(status: int) -> HTTPError:
    response = Response()
    response.status_code = status
    return HTTPError("rejected", response=response)


def _github(reject: bool) -> tuple[Any, list[str]]:
    from unittest.mock import patch

    from reviewloop.vcs.github import GitHubAdapter

    with patch("reviewloop.vcs.github.Github"):
        adapter = GitHubAdapter("ghp_FAKE", "o/r", Path("/nonexistent"), "issue_comment")
    bodies: list[str] = []
    pr = MagicMock()
    pr.head.sha = "h" * 40

    def review(body: str, **_kw: Any) -> Any:
        bodies.append(body)
        if reject:
            raise GithubException(422, {}, {})
        return MagicMock(id=1)

    def issue(body: str) -> Any:
        bodies.append(body)
        return MagicMock(id=2)

    pr.create_review_comment.side_effect = review
    pr.create_issue_comment.side_effect = issue
    adapter._pr = pr
    return adapter, bodies


def _gitlab(reject: bool) -> tuple[Any, list[str]]:
    from reviewloop.vcs.gitlab import GitLabAdapter

    gl = MagicMock()
    mr = MagicMock()
    mr.diff_refs = {"base_sha": "b", "start_sha": "s", "head_sha": "h"}
    bodies: list[str] = []

    def create(data: dict[str, Any]) -> Any:
        bodies.append(data["body"])
        if reject and "position" in data:
            raise GitlabCreateError("bad position", 400)
        return MagicMock(id="d", attributes={"notes": [{"id": 1}]})

    mr.discussions.create.side_effect = create
    gl.projects.get.return_value.mergerequests.get.return_value = mr
    adapter = GitLabAdapter("https://gl.example", "fake", "1", {"CI_MERGE_REQUEST_IID": "1"}, client=gl)
    return adapter, bodies


def _bitbucket_cloud(reject: bool) -> tuple[Any, list[str]]:
    from reviewloop.vcs.bitbucket_cloud import BitbucketCloudAdapter

    client = MagicMock()
    bodies: list[str] = []

    def post(_path: str, json: dict[str, Any]) -> Any:
        bodies.append(json["content"]["raw"])
        if reject and "inline" in json:
            raise _http_error(422)
        return {"id": 1}

    client.post.side_effect = post
    return BitbucketCloudAdapter("ws", "repo", "fake", {"BITBUCKET_PR_ID": "1"}, client=client), bodies


def _bitbucket_dc(reject: bool) -> tuple[Any, list[str]]:
    from reviewloop.vcs.bitbucket_dc import BitbucketDCAdapter

    client = MagicMock()
    bodies: list[str] = []

    def post(_path: str, data: dict[str, Any]) -> Any:
        bodies.append(data["text"])
        if reject and "anchor" in data:
            raise _http_error(400)
        return {"id": 1}

    client.post.side_effect = post
    return BitbucketDCAdapter("https://bb", "fake", "P", "r", {"BB_PR_ID": "1"}, client=client), bodies


@pytest.mark.parametrize("make", [_github, _gitlab, _bitbucket_cloud, _bitbucket_dc])
@pytest.mark.parametrize("reject", [False, True])
@pytest.mark.parametrize("anchor", ANCHORS, ids=["new", "range", "old", "general"])
def test_every_posted_finding_body_ends_with_its_marker(make: Any, reject: bool, anchor: Anchor) -> None:
    adapter, bodies = make(reject)
    adapter.post_inline(_finding(anchor), anchor)
    assert bodies
    for body in bodies:
        assert body.endswith(REAL), body
        marker = parse_finding_marker(body)
        assert marker is not None and marker.id == "f-real"
