from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch

import httpx
import pytest
from github import GithubException

from reviewloop.errors import ReviewloopError
from reviewloop.models import Anchor, Command, EventKind, Finding, Severity, Side, ThreadRef, User
from reviewloop.vcs.github import GitHubAdapter

FIXTURES = Path(__file__).parents[2] / "fixtures" / "github"
STATE = '<!-- reviewloop:state {"v":1,"sha":"a","base":"b","turn":1,"open":[]} -->'
MARKER = '<!-- reviewloop:finding {"v":1,"id":"f-1","fp":"abc","sev":"high","rule":null} -->'


def make(fixture: str, name: str = "issue_comment"):
    with patch("reviewloop.vcs.github.Github") as gh_cls:
        gh = gh_cls.return_value
        adapter = GitHubAdapter("ghp_FAKE", "o/r", FIXTURES / fixture, name)
    return adapter, gh, gh.get_repo.return_value


def finding() -> Finding:
    return Finding(path="a.py", line=3, severity=Severity.HIGH, category="bug", text="body", existing_code="x")


def test_event_pr_opened():
    ev = make("pr_opened.json", "pull_request")[0].event()
    assert (ev.kind, ev.pr_number, ev.actor) == (EventKind.OPENED, 5, User(id="7", username="alice"))


def test_event_ready_for_review():
    assert make("pr_ready.json", "pull_request")[0].event().kind == EventKind.OPENED


def test_event_draft_ignored():
    assert make("pr_draft.json", "pull_request")[0].event().kind == EventKind.IGNORED


def test_event_other_name_ignored():
    assert make("pr_opened.json", "push")[0].event().kind == EventKind.IGNORED


def test_event_command_review():
    ev = make("comment_review.json")[0].event()
    assert (ev.kind, ev.command, ev.pr_number) == (EventKind.COMMAND, Command.REVIEW, 5)


@pytest.mark.parametrize("fx", ["comment_plain.json", "comment_unknown.json"])
def test_event_non_command_comment_ignored(fx):
    assert make(fx)[0].event().kind == EventKind.IGNORED


def test_event_comment_on_issue_not_pr_ignored():
    assert make("comment_issue.json")[0].event().kind == EventKind.IGNORED


def test_can_write_uses_author_association():
    alice = User(id="7", username="alice")
    assert make("comment_review.json")[0].can_write(alice)
    adapter, _, repo = make("comment_contributor.json")
    assert not adapter.can_write(alice)
    repo.get_collaborator_permission.assert_not_called()


@pytest.mark.parametrize(("perm", "ok"), [("write", True), ("maintain", True), ("read", False)])
def test_can_write_other_user_uses_permission(perm, ok):
    adapter, _, repo = make("comment_review.json")
    repo.get_collaborator_permission.return_value = perm
    assert adapter.can_write(User(id="9", username="bob")) is ok


def _pull(adapter, repo, **kw):
    pr = MagicMock()
    pr.number = 5
    pr.head.sha = "h1"
    for k, v in kw.items():
        setattr(pr, k, v)
    repo.get_pull.return_value = pr
    return pr


def test_pull_request_fork_detection():
    adapter, _, repo = make("comment_review.json")
    pr = _pull(adapter, repo, title="t", body=None, html_url="u")
    pr.head.ref, pr.base.ref, pr.base.sha = "feat", "main", "b1"
    pr.user = NS(id=7, login="alice")
    pr.head.repo.full_name = "fork/r"
    pr.base.repo.full_name = "o/r"
    model = adapter.pull_request()
    assert model.is_fork and model.body == "" and model.head_sha == "h1"
    pr.head.repo = None
    adapter._pr = None
    assert adapter.pull_request().is_fork


def test_bot_identity_fallback():
    adapter, gh, _ = make("comment_review.json")
    gh.get_user.side_effect = GithubException(403, {}, {})
    assert adapter.bot_identity().username == "github-actions[bot]"


def _rc(id, user, body, reply_to=None, line=3):
    return NS(
        id=id,
        user=NS(id=1, login=user),
        body=body,
        in_reply_to_id=reply_to,
        path="a.py",
        line=line,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _gql(nodes, has_next=False):
    resp = MagicMock()
    resp.json.return_value = {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {"pageInfo": {"hasNextPage": has_next, "endCursor": "c"}, "nodes": nodes}
                }
            }
        }
    }
    return resp


def test_threads_parse_markers():
    adapter, gh, repo = make("comment_review.json")
    gh.get_user.return_value = NS(id=1, login="bot")
    pr = _pull(adapter, repo)
    pr.get_review_comments.return_value = [
        _rc(10, "bot", f"issue\n{MARKER}"),
        _rc(11, "alice", "why?", reply_to=10),
        _rc(20, "alice", "human thread"),
    ]
    node = {"id": "PRRT_1", "isResolved": True, "comments": {"nodes": [{"databaseId": 10}]}}
    with patch("reviewloop.vcs.github.httpx.post", return_value=_gql([node])):
        threads = adapter.threads()
    assert len(threads) == 1
    t = threads[0]
    assert t.ref == ThreadRef(id="PRRT_1", note_id="10")
    assert t.resolved and t.marker is not None and t.marker.id == "f-1"
    assert [r.body for r in t.replies] == ["why?"]


def test_threads_invalid_marker_is_none():
    adapter, gh, repo = make("comment_review.json")
    gh.get_user.return_value = NS(id=1, login="bot")
    pr = _pull(adapter, repo)
    pr.get_review_comments.return_value = [_rc(10, "bot", "<!-- reviewloop:finding {bad} -->")]
    with patch("reviewloop.vcs.github.httpx.post", return_value=_gql([])):
        t = adapter.threads()[0]
    assert t.marker is None and not t.resolved


def test_post_inline_uses_review_comment_with_side():
    adapter, _, repo = make("comment_review.json")
    pr = _pull(adapter, repo)
    pr.create_review_comment.return_value = NS(id=99)
    ref = adapter.post_inline(finding(), Anchor(path="a.py", line=3, side=Side.OLD, in_diff=True))
    assert ref.note_id == "99"
    kw = pr.create_review_comment.call_args.kwargs
    assert (kw["commit"], kw["path"], kw["line"], kw["side"]) == ("h1", "a.py", 3, "LEFT")


def _inline_pr(adapter, repo):
    pr = _pull(adapter, repo)
    pr.create_review_comment.return_value = NS(id=99)
    return pr


NEW = Anchor(path="a.py", line=10, side=Side.NEW, in_diff=True)


RANGE = Anchor(path="a.py", line=10, start_line=8, side=Side.NEW, in_diff=True)


def test_post_inline_multiline_sets_start_line():
    adapter, _, repo = make("comment_review.json")
    pr = _inline_pr(adapter, repo)
    # The range comes from the anchor, not from counting the quoted lines.
    adapter.post_inline(finding(), RANGE)
    kw = pr.create_review_comment.call_args.kwargs
    assert (kw["start_line"], kw["line"], kw["start_side"]) == (8, 10, "RIGHT")


def test_post_inline_multiline_old_side_and_quote_length_ignored():
    adapter, _, repo = make("comment_review.json")
    pr = _inline_pr(adapter, repo)
    f = finding().model_copy(update={"existing_code": "a\nb\nc\nd\ne"})
    adapter.post_inline(f, Anchor(path="a.py", line=6, start_line=5, side=Side.OLD, in_diff=True))
    kw = pr.create_review_comment.call_args.kwargs
    assert (kw["start_line"], kw["line"], kw["start_side"], kw["side"]) == (5, 6, "LEFT", "LEFT")
    f = finding().model_copy(update={"existing_code": "a\nb\nc"})
    adapter.post_inline(f, NEW)
    assert "start_line" not in pr.create_review_comment.call_args.kwargs


def test_post_inline_attaches_suggestion_when_eligible():
    adapter, _, repo = make("comment_review.json")
    pr = _inline_pr(adapter, repo)
    adapter.post_inline(finding().model_copy(update={"suggestion": "y = 1"}), NEW)
    kw = pr.create_review_comment.call_args.kwargs
    assert kw["body"] == "body\n\n```suggestion\ny = 1\n```"
    assert "start_line" not in kw


def test_post_inline_no_suggestion_for_security():
    adapter, _, repo = make("comment_review.json")
    pr = _inline_pr(adapter, repo)
    f = finding().model_copy(update={"suggestion": "y", "security": True})
    adapter.post_inline(f, NEW)
    assert pr.create_review_comment.call_args.kwargs["body"] == "body"


def test_post_inline_no_suggestion_over_30_lines():
    adapter, _, repo = make("comment_review.json")
    pr = _inline_pr(adapter, repo)
    f = finding().model_copy(update={"suggestion": "y"})
    adapter.post_inline(f, Anchor(path="a.py", line=40, start_line=10, side=Side.NEW, in_diff=True))
    assert pr.create_review_comment.call_args.kwargs["body"] == "body"
    adapter.post_inline(f, Anchor(path="a.py", line=39, start_line=10, side=Side.NEW, in_diff=True))
    assert pr.create_review_comment.call_args.kwargs["body"] == "body\n\n```suggestion\ny\n```"


def test_post_inline_file_level_when_outside_diff():
    adapter, _, repo = make("comment_review.json")
    pr = _pull(adapter, repo)
    pr.create_review_comment.return_value = NS(id=99)
    adapter.post_inline(finding(), Anchor(path="a.py", line=3, side=Side.NEW, in_diff=False))
    kw = pr.create_review_comment.call_args.kwargs
    assert kw["subject_type"] == "file" and "line" not in kw


def test_post_general_and_reply():
    adapter, _, repo = make("comment_review.json")
    pr = _pull(adapter, repo)
    pr.create_issue_comment.return_value = NS(id=5)
    assert adapter.post_general("hi").id == "5"
    adapter.reply(ThreadRef(id="PRRT_1", note_id="10"), "ok")
    pr.create_review_comment_reply.assert_called_once_with(10, "ok")
    with pytest.raises(ReviewloopError):
        adapter.reply(ThreadRef(id="PRRT_1"), "ok")


def test_upsert_summary_edits_existing_marker_comment():
    adapter, gh, repo = make("comment_review.json")
    gh.get_user.return_value = NS(id=1, login="bot")
    pr = _pull(adapter, repo)
    other = MagicMock(user=NS(login="alice"), body="<!-- reviewloop:summary -->")
    mine = MagicMock(user=NS(login="bot"), body="old\n<!-- reviewloop:summary -->")
    pr.get_issue_comments.return_value = [other, mine]
    adapter.upsert_summary("new")
    mine.edit.assert_called_once_with("new\n<!-- reviewloop:summary -->")
    other.edit.assert_not_called()
    pr.create_issue_comment.assert_not_called()


def test_upsert_summary_creates_when_missing():
    adapter, gh, repo = make("comment_review.json")
    gh.get_user.return_value = NS(id=1, login="bot")
    pr = _pull(adapter, repo)
    pr.get_issue_comments.return_value = []
    adapter.upsert_summary(STATE)
    pr.create_issue_comment.assert_called_once_with(STATE)


def test_suggestion_fence_longer_than_content():
    adapter = make("comment_review.json")[0]
    anchor = Anchor(path="a", line=1, side=Side.NEW, in_diff=True)
    assert adapter.suggestion_block(anchor, "x") == "```suggestion\nx\n```"
    assert adapter.suggestion_block(anchor, "a ``` b").startswith("````suggestion\n")


def test_suggestion_none_on_old_side_or_outside_diff():
    adapter = make("comment_review.json")[0]
    assert adapter.suggestion_block(Anchor(path="a", line=1, side=Side.OLD, in_diff=True), "x") is None
    assert adapter.suggestion_block(Anchor(path="a", line=1, side=Side.NEW, in_diff=False), "x") is None


def test_resolve_uses_graphql_resolve_review_thread():
    adapter = make("comment_review.json")[0]
    with patch("reviewloop.vcs.github.httpx.post") as post:
        post.return_value.json.return_value = {"data": {}}
        adapter.resolve(ThreadRef(id="PRRT_1", note_id="10"))
    sent = post.call_args.kwargs["json"]
    assert "resolveReviewThread" in sent["query"] and sent["variables"] == {"id": "PRRT_1"}


def test_resolve_looks_up_thread_id_for_fresh_comment():
    adapter, _, repo = make("comment_review.json")
    _pull(adapter, repo)
    node = {"id": "PRRT_9", "isResolved": False, "comments": {"nodes": [{"databaseId": 99}]}}
    with patch("reviewloop.vcs.github.httpx.post") as post:
        post.side_effect = [_gql([node]), MagicMock(json=lambda: {"data": {}})]
        adapter.resolve(ThreadRef(id="99", note_id="99"))
    assert post.call_args.kwargs["json"]["variables"] == {"id": "PRRT_9"}
    with (
        patch("reviewloop.vcs.github.httpx.post", return_value=_gql([])),
        pytest.raises(ReviewloopError),
    ):
        adapter.resolve(ThreadRef(id="1", note_id="1"))


def test_graphql_errors_raise():
    adapter = make("comment_review.json")[0]
    with patch("reviewloop.vcs.github.httpx.post") as post:
        post.return_value.json.return_value = {"errors": [{"message": "no"}]}
        with pytest.raises(ReviewloopError):
            adapter.resolve(ThreadRef(id="PRRT_1"))


def test_read_file():
    adapter, _, repo = make("comment_review.json")
    repo.get_contents.return_value = NS(decoded_content=b"hi")
    assert adapter.read_file("a", "h1") == "hi"
    repo.get_contents.return_value = []
    assert adapter.read_file("a", "h1") is None
    repo.get_contents.side_effect = GithubException(404, {}, {})
    assert adapter.read_file("a", "h1") is None


@pytest.mark.parametrize("content", ["{not json", "[1, 2]", None])
def test_event_bad_payload_raises(tmp_path, content):
    path = tmp_path / "event.json"
    if content is not None:
        path.write_text(content)
    adapter = make(str(path), "pull_request")[0]
    with pytest.raises(ReviewloopError):
        adapter.event()


def test_event_null_pull_request_ignored(tmp_path):
    path = tmp_path / "event.json"
    path.write_text('{"action": "opened", "pull_request": null}')
    assert make(str(path), "pull_request")[0].event().kind == EventKind.IGNORED


def test_bot_identity_non_403_raises():
    adapter, gh, _ = make("comment_review.json")
    gh.get_user.side_effect = GithubException(500, {}, {})
    with pytest.raises(ReviewloopError):
        adapter.bot_identity()


def test_can_write_permission_404_is_false():
    adapter, _, repo = make("comment_review.json")
    repo.get_collaborator_permission.side_effect = GithubException(404, {}, {})
    assert adapter.can_write(User(id="9", username="bob")) is False
    repo.get_collaborator_permission.side_effect = GithubException(500, {}, {})
    with pytest.raises(ReviewloopError):
        adapter.can_write(User(id="9", username="bob"))


def test_resolve_finds_thread_on_second_graphql_page():
    adapter, _, repo = make("comment_review.json")
    _pull(adapter, repo)
    other = {"id": "PRRT_1", "isResolved": False, "comments": {"nodes": [{"databaseId": 1}]}}
    target = {"id": "PRRT_2", "isResolved": False, "comments": {"nodes": [{"databaseId": 99}]}}
    with patch("reviewloop.vcs.github.httpx.post") as post:
        post.side_effect = [
            _gql([other], has_next=True),
            _gql([target]),
            MagicMock(json=lambda: {"data": {}}),
        ]
        adapter.resolve(ThreadRef(id="99", note_id="99"))
    assert post.call_args_list[1].kwargs["json"]["variables"]["cursor"] == "c"
    assert post.call_args.kwargs["json"]["variables"] == {"id": "PRRT_2"}


def test_graphql_http_error_hides_token():
    adapter = make("comment_review.json")[0]
    request = httpx.Request("POST", "https://api.github.com/graphql")
    err = httpx.HTTPStatusError("boom", request=request, response=httpx.Response(502, request=request))
    with patch("reviewloop.vcs.github.httpx.post") as post:
        post.return_value.raise_for_status.side_effect = err
        with pytest.raises(ReviewloopError) as info:
            adapter.resolve(ThreadRef(id="PRRT_1"))
    assert "ghp_FAKE" not in str(info.value) and "502" in str(info.value)


def test_graphql_null_data_raises():
    adapter = make("comment_review.json")[0]
    with patch("reviewloop.vcs.github.httpx.post") as post:
        post.return_value.json.return_value = {"data": None}
        with pytest.raises(ReviewloopError):
            adapter.resolve(ThreadRef(id="PRRT_1"))


def test_post_inline_422_falls_back_to_general_comment():
    adapter, _, repo = make("comment_review.json")
    pr = _pull(adapter, repo)
    pr.create_review_comment.side_effect = GithubException(422, {}, {})
    pr.create_issue_comment.return_value = NS(id=55)
    ref = adapter.post_inline(finding(), NEW)
    assert ref.id == "55"
    assert pr.create_issue_comment.call_args.args[0].startswith("`a.py:10`")
    pr.create_review_comment.side_effect = GithubException(500, {}, {})
    with pytest.raises(GithubException):
        adapter.post_inline(finding(), NEW)


def test_post_inline_422_fallback_cites_range_without_suggestion():
    adapter, _, repo = make("comment_review.json")
    pr = _pull(adapter, repo)
    pr.create_review_comment.side_effect = GithubException(422, {}, {})
    pr.create_issue_comment.return_value = NS(id=55)
    f = finding().model_copy(update={"existing_code": "a", "suggestion": "y"})
    adapter.post_inline(f, Anchor(path="a.py", line=10, start_line=9, side=Side.NEW, in_diff=True))
    sent = pr.create_issue_comment.call_args.args[0]
    assert sent.startswith("`a.py:9-10`")
    assert "```suggestion" not in sent
    assert sent.endswith("body")


def test_summary_body_returns_bot_summary_only():
    adapter, gh, repo = make("comment_review.json")
    gh.get_user.return_value = NS(id=1, login="bot")
    pr = _pull(adapter, repo)
    other = MagicMock(user=NS(login="alice"), body=STATE)
    mine = MagicMock(user=NS(login="bot"), body=f"done\n{STATE}")
    pr.get_issue_comments.return_value = [other]
    assert adapter.summary_body() is None
    pr.get_issue_comments.return_value = [other, mine]
    assert adapter.summary_body() == f"done\n{STATE}"


def test_pull_request_draft_flag():
    adapter, _, repo = make("comment_review.json")
    pr = _pull(adapter, repo, title="t", body="b", html_url="u", draft=True)
    pr.head.ref, pr.base.ref, pr.base.sha = "feat", "main", "b1"
    pr.user = NS(id=7, login="alice")
    assert adapter.pull_request().draft is True
