from typing import Any
from unittest.mock import MagicMock

import pytest
from requests import ConnectionError as RequestsConnectionError
from requests import HTTPError, Response

from reviewloop.errors import ReviewloopError
from reviewloop.models import Anchor, Command, EventKind, Finding, Severity, Side, ThreadRef, User
from reviewloop.vcs.bitbucket_cloud import SUMMARY_MARKER, BitbucketCloudAdapter

FAKE_TOKEN = "fake-" + "token"
BOT = {"uuid": "{bot}", "nickname": "bot"}
ALICE = {"uuid": "{alice}", "nickname": "alice"}
REPO = "repositories/ws/repo"
COMMENTS = f"{REPO}/pullrequests/42/comments"
PR_ENV = {"BITBUCKET_PR_ID": "42"}


def http_error(status: int) -> HTTPError:
    resp = Response()
    resp.status_code = status
    return HTTPError("boom", response=resp)


def comment(cid: int, user: dict[str, str], body: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": cid,
        "user": user,
        "content": {"raw": body},
        "created_on": "2026-01-01T00:00:00+00:00",
        **extra,
    }


def make(env: dict[str, str], comments: list[dict[str, Any]] | None = None) -> tuple[BitbucketCloudAdapter, MagicMock]:
    client = MagicMock()
    routes: dict[str, Any] = {
        "user": BOT,
        COMMENTS: {"values": comments or []},
        f"{REPO}/pullrequests/42": {
            "title": "T",
            "description": None,
            "source": {
                "commit": {"hash": "abc"},
                "branch": {"name": "feat"},
                "repository": {"full_name": "ws/repo"},
            },
            "destination": {
                "commit": {"hash": "dst"},
                "branch": {"name": "main"},
                "repository": {"full_name": "ws/repo"},
            },
            "author": ALICE,
            "links": {"html": {"href": "https://bb/pr/42"}},
        },
        f"{REPO}/commit/abc": {"hash": "abcfull"},
        f"{REPO}/merge-base/dst..abcfull": {"hash": "basefull"},
    }

    def get(path: str, **kwargs: Any) -> Any:
        if path == COMMENTS:
            assert kwargs["params"] == {"pagelen": 100}
        return routes[path]

    client.get.side_effect = get
    client.post.return_value = {"id": 500}
    return BitbucketCloudAdapter("ws", "repo", FAKE_TOKEN, env, client=client), client


def finding(**kw: Any) -> Finding:
    base: dict[str, Any] = {
        "path": "a.py",
        "line": 3,
        "severity": Severity.HIGH,
        "category": "bug",
        "text": "problem",
        "existing_code": "x",
    }
    return Finding(**{**base, **kw})


def anchor(**kw: Any) -> Anchor:
    return Anchor(**{"path": "a.py", "line": 3, "side": Side.NEW, "in_diff": True, **kw})


def test_from_env_reads_variables() -> None:
    env = {
        "BITBUCKET_WORKSPACE": "ws",
        "BITBUCKET_REPO_SLUG": "repo",
        "REVIEWLOOP_BITBUCKET_TOKEN": FAKE_TOKEN,
    }
    assert isinstance(BitbucketCloudAdapter.from_env(env), BitbucketCloudAdapter)


def test_event_pr_pipeline_without_summary_is_opened() -> None:
    adapter, _ = make(PR_ENV)
    event = adapter.event()
    assert event.kind is EventKind.OPENED
    assert event.pr_number == 42


def test_event_pr_pipeline_with_summary_is_ignored() -> None:
    adapter, _ = make(PR_ENV, [comment(1, BOT, f"x\n{SUMMARY_MARKER}")])
    assert adapter.event().kind is EventKind.IGNORED


def test_event_summary_from_non_bot_does_not_skip() -> None:
    adapter, _ = make(PR_ENV, [comment(1, ALICE, SUMMARY_MARKER)])
    assert adapter.event().kind is EventKind.OPENED


def test_event_no_pr_is_ignored() -> None:
    adapter, _ = make({})
    assert adapter.event().kind is EventKind.IGNORED


def test_event_malformed_pr_id_is_ignored() -> None:
    adapter, _ = make({"BITBUCKET_PR_ID": "abc"})
    assert adapter.event().kind is EventKind.IGNORED


def test_event_custom_pipeline_command() -> None:
    env = {"PR_ID": "42", "COMMAND": "recheck", "BITBUCKET_STEP_TRIGGERER_UUID": "{alice}"}
    adapter, _ = make(env)
    event = adapter.event()
    assert event.kind is EventKind.COMMAND
    assert event.command is Command.RECHECK
    assert event.pr_number == 42
    assert event.actor == User(id="{alice}", username="{alice}")


def test_event_unknown_command_is_ignored() -> None:
    adapter, _ = make({"PR_ID": "42", "COMMAND": "dance", "BITBUCKET_STEP_TRIGGERER_UUID": "{a}"})
    assert adapter.event().kind is EventKind.IGNORED


def test_event_command_without_actor_is_ignored() -> None:
    adapter, _ = make({"PR_ID": "42", "COMMAND": "review"})
    assert adapter.event().kind is EventKind.IGNORED


def test_event_custom_pipeline_without_command_is_ignored() -> None:
    adapter, _ = make({"PR_ID": "42"})
    assert adapter.event().kind is EventKind.IGNORED


def test_pull_request_maps_fields() -> None:
    adapter, _ = make(PR_ENV)
    pr = adapter.pull_request()
    assert (pr.number, pr.head_sha, pr.base_sha) == (42, "abcfull", "basefull")
    assert (pr.source_branch, pr.target_branch, pr.body) == ("feat", "main", "")
    assert pr.author == User(id="{alice}", username="alice")
    assert pr.is_fork is False
    assert adapter.pull_request() is pr


def test_pull_request_without_id_raises() -> None:
    adapter, _ = make({})
    with pytest.raises(ReviewloopError):
        adapter.pull_request()


def test_bot_identity_cached() -> None:
    adapter, client = make(PR_ENV)
    assert adapter.bot_identity() == User(id="{bot}", username="bot")
    adapter.bot_identity()
    assert client.get.call_count == 1


def test_bot_identity_from_env_skips_api() -> None:
    adapter, client = make({**PR_ENV, "REVIEWLOOP_BITBUCKET_BOT_UUID": "{botenv}"})
    assert adapter.bot_identity() == User(id="{botenv}", username="{botenv}")
    client.get.assert_not_called()


@pytest.mark.parametrize("status", [401, 403])
def test_bot_identity_denied_names_variable(status: int) -> None:
    adapter, client = make(PR_ENV)
    client.get.side_effect = http_error(status)
    with pytest.raises(ReviewloopError, match="REVIEWLOOP_BITBUCKET_BOT_UUID"):
        adapter.bot_identity()


def test_bot_identity_other_error_raises_plain() -> None:
    adapter, client = make(PR_ENV)
    client.get.side_effect = http_error(500)
    with pytest.raises(ReviewloopError, match="500") as err:
        adapter.bot_identity()
    assert "REVIEWLOOP_BITBUCKET_BOT_UUID" not in str(err.value)


def test_user_without_uuid_is_malformed() -> None:
    adapter, client = make(PR_ENV)
    client.get.side_effect = None
    client.get.return_value = {"nickname": "x"}
    with pytest.raises(ReviewloopError, match="uuid"):
        adapter.bot_identity()


def test_network_error_is_wrapped() -> None:
    adapter, client = make(PR_ENV)
    client.get.side_effect = RequestsConnectionError("down")
    with pytest.raises(ReviewloopError, match="network error"):
        adapter.bot_identity()


@pytest.mark.parametrize("raw", ["0", "-1", "4\u0662", "abc", " 4", "4.0"])
def test_event_pr_id_must_be_ascii_positive(raw: str) -> None:
    adapter, _ = make({"BITBUCKET_PR_ID": raw})
    assert adapter.event().kind is EventKind.IGNORED


def test_api_error_hides_token() -> None:
    adapter, client = make(PR_ENV)
    client.get.side_effect = http_error(500)
    with pytest.raises(ReviewloopError) as err:
        adapter.bot_identity()
    assert FAKE_TOKEN not in str(err.value)
    assert "500" in str(err.value)


def test_can_write_permissions() -> None:
    adapter, client = make(PR_ENV)
    for perm, expected in (("write", True), ("admin", True), ("read", False)):
        client.get.side_effect = None
        client.get.return_value = {"permission": perm}
        assert adapter.can_write(User(id="{alice}", username="alice")) is expected


def test_can_write_404_false_other_raises() -> None:
    adapter, client = make(PR_ENV)
    client.get.side_effect = http_error(404)
    assert adapter.can_write(User(id="{x}", username="x")) is False
    client.get.side_effect = http_error(403)
    with pytest.raises(ReviewloopError, match="repository:admin"):
        adapter.can_write(User(id="{x}", username="x"))
    client.get.side_effect = http_error(500)
    with pytest.raises(ReviewloopError):
        adapter.can_write(User(id="{x}", username="x"))


def test_threads_parse_markers() -> None:
    marker = '<!-- reviewloop:finding {"v":1,"id":"f1","fp":"abc","sev":"high","rule":null} -->'
    comments = [
        comment(1, BOT, f"body\n{marker}", inline={"path": "a.py", "to": 7, "from": None}),
        comment(2, ALICE, "reply", parent={"id": 1}),
        comment(3, ALICE, "nested", parent={"id": 2}),
        comment(4, ALICE, "human comment", inline={"path": "b.py", "to": 1}),
        comment(5, BOT, "old side", inline={"path": "c.py", "to": None, "from": 4}, resolution={"type": "x"}),
        comment(6, BOT, "gone", inline={"path": "d.py", "to": 1}, deleted=True),
        comment(7, BOT, "general no marker"),
    ]
    threads = make(PR_ENV, comments)[0].threads()
    assert [t.ref.id for t in threads] == ["1", "5"]
    first, second = threads
    assert (first.path, first.line, first.resolved) == ("a.py", 7, False)
    assert first.marker is not None
    assert first.marker.id == "f1"
    assert [r.body for r in first.replies] == ["reply", "nested"]
    assert (second.path, second.line, second.resolved) == ("c.py", 4, True)
    assert (first.side, second.side) == (Side.NEW, Side.OLD)


def test_threads_follow_pagination() -> None:
    adapter, client = make(PR_ENV)
    pages = {
        COMMENTS: {"values": [comment(1, BOT, "a", inline={"path": "a", "to": 1})], "next": "https://next"},
        "https://next": {"values": [comment(2, BOT, "b", inline={"path": "b", "to": 2})]},
    }

    def get(path: str, **kwargs: Any) -> Any:
        if path == "https://next":
            assert kwargs == {"absolute": True}
        return BOT if path == "user" else pages[path]

    client.get.side_effect = get
    assert [t.ref.id for t in adapter.threads()] == ["1", "2"]


def test_post_inline_new_side() -> None:
    adapter, client = make(PR_ENV)
    ref = adapter.post_inline(finding(suggestion="y = 1"), anchor(start_line=1))
    assert ref == ThreadRef(id="500", note_id="500")
    client.post.assert_called_once_with(
        COMMENTS, json={"content": {"raw": "problem"}, "inline": {"path": "a.py", "to": 3}}
    )


def test_post_inline_old_side() -> None:
    adapter, client = make(PR_ENV)
    adapter.post_inline(finding(), anchor(side=Side.OLD))
    assert client.post.call_args.kwargs["json"]["inline"] == {"path": "a.py", "from": 3}


def test_post_inline_rejected_falls_back_to_general() -> None:
    adapter, client = make(PR_ENV)
    client.post.side_effect = [http_error(400), {"id": 9}]
    ref = adapter.post_inline(finding(), anchor(start_line=1))
    assert ref.id == "9"
    assert client.post.call_args.kwargs["json"] == {"content": {"raw": "`a.py:1-3`\n\nproblem"}}


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_post_inline_non_rejection_errors_raise(status: int) -> None:
    adapter, client = make(PR_ENV)
    client.post.side_effect = http_error(status)
    with pytest.raises(ReviewloopError):
        adapter.post_inline(finding(), anchor())
    assert client.post.call_count == 1


@pytest.mark.parametrize("status", [404, 409, 422])
def test_post_inline_rejection_statuses_fall_back(status: int) -> None:
    adapter, client = make(PR_ENV)
    client.post.side_effect = [http_error(status), {"id": 9}]
    assert adapter.post_inline(finding(), anchor()).id == "9"


def test_post_inline_not_in_diff_is_general() -> None:
    adapter, client = make(PR_ENV)
    adapter.post_inline(finding(), anchor(in_diff=False))
    assert client.post.call_args.kwargs["json"] == {"content": {"raw": "`a.py:3`\n\nproblem"}}


def test_post_general_and_reply() -> None:
    adapter, client = make(PR_ENV)
    adapter.post_general("hello")
    assert client.post.call_args.kwargs["json"] == {"content": {"raw": "hello"}}
    adapter.reply(ThreadRef(id="7"), "re")
    assert client.post.call_args.kwargs["json"] == {"content": {"raw": "re"}, "parent": {"id": 7}}


def test_resolve_posts_resolve_endpoint() -> None:
    adapter, client = make(PR_ENV)
    adapter.resolve(ThreadRef(id="7"))
    client.post.assert_called_once_with(f"{COMMENTS}/7/resolve")


def test_resolve_error_raises() -> None:
    adapter, client = make(PR_ENV)
    client.post.side_effect = http_error(403)
    with pytest.raises(ReviewloopError):
        adapter.resolve(ThreadRef(id="7"))


def test_upsert_summary_creates_with_marker() -> None:
    adapter, client = make(PR_ENV)
    adapter.upsert_summary("sum")
    assert client.post.call_args.kwargs["json"] == {"content": {"raw": f"sum\n{SUMMARY_MARKER}"}}


def test_upsert_summary_updates_existing_bot_comment() -> None:
    adapter, client = make(PR_ENV, [comment(1, ALICE, SUMMARY_MARKER), comment(2, BOT, SUMMARY_MARKER)])
    adapter.upsert_summary(f"new\n{SUMMARY_MARKER}")
    client.put.assert_called_once_with(f"{COMMENTS}/2", data={"content": {"raw": f"new\n{SUMMARY_MARKER}"}})
    client.post.assert_not_called()


def test_upsert_summary_keeps_state_marker_only() -> None:
    state = '<!-- reviewloop:state {"v":1,"sha":"a","base":"b","turn":1,"open":[]} -->'
    adapter, client = make(PR_ENV)
    adapter.upsert_summary(f"sum\n{state}")
    assert client.post.call_args.kwargs["json"]["content"]["raw"] == f"sum\n{state}"


def test_no_suggestion_block() -> None:
    adapter, _ = make(PR_ENV)
    assert adapter.suggestion_block(anchor(), "x = 1") is None


def test_read_file_ok_missing_error() -> None:
    adapter, client = make(PR_ENV)
    client.get.side_effect = None
    client.get.return_value = b"hello"
    assert adapter.read_file("dir/a b.py", "main") == "hello"
    assert client.get.call_args.args[0] == f"{REPO}/src/main/dir/a%20b.py"
    client.get.side_effect = http_error(404)
    assert adapter.read_file("x", "main") is None
    client.get.side_effect = http_error(500)
    with pytest.raises(ReviewloopError):
        adapter.read_file("x", "main")


def test_summary_on_second_page_is_found_and_updated() -> None:
    adapter, client = make(PR_ENV)
    pages = {
        COMMENTS: {"values": [comment(1, ALICE, "hi")], "next": "https://next"},
        "https://next": {"values": [comment(2, BOT, "gone", deleted=True), comment(3, BOT, SUMMARY_MARKER)]},
    }
    client.get.side_effect = lambda path, **_: BOT if path == "user" else pages[path]
    assert adapter.event().kind is EventKind.IGNORED
    adapter.upsert_summary("new")
    client.put.assert_called_once_with(f"{COMMENTS}/3", data={"content": {"raw": f"new\n{SUMMARY_MARKER}"}})


def test_deleted_summary_is_skipped() -> None:
    adapter, _ = make(PR_ENV, [comment(1, BOT, SUMMARY_MARKER, deleted=True)])
    assert adapter.event().kind is EventKind.OPENED


def test_summary_scan_memoized_between_event_and_upsert() -> None:
    adapter, client = make(PR_ENV)
    adapter.event()
    scans = sum(1 for c in client.get.call_args_list if c.args[0] == COMMENTS)
    adapter.upsert_summary("s")
    assert scans == 1
    assert sum(1 for c in client.get.call_args_list if c.args[0] == COMMENTS) == 2


def test_env_uuid_without_braces_uppercase_matches_bot_summary() -> None:
    env = {**PR_ENV, "REVIEWLOOP_BITBUCKET_BOT_UUID": "BOT"}
    adapter, client = make(env, [comment(1, BOT, SUMMARY_MARKER)])
    assert adapter.bot_identity() == User(id="{bot}", username="{bot}")
    assert adapter.event().kind is EventKind.IGNORED
    adapter.upsert_summary("new")
    client.post.assert_not_called()


def test_read_file_network_error_message() -> None:
    adapter, client = make(PR_ENV)
    client.get.side_effect = RequestsConnectionError("down")
    with pytest.raises(ReviewloopError, match="network error"):
        adapter.read_file("x", "main")


def test_summary_body_returns_bot_comment_only() -> None:
    adapter, _ = make(PR_ENV, [comment(1, ALICE, SUMMARY_MARKER)])
    assert adapter.summary_body() is None
    adapter, _ = make(PR_ENV, [comment(1, ALICE, SUMMARY_MARKER), comment(2, BOT, f"done\n{SUMMARY_MARKER}")])
    assert adapter.summary_body() == f"done\n{SUMMARY_MARKER}"


def test_pull_request_draft_flag() -> None:
    adapter, client = make(PR_ENV)
    assert adapter.pull_request().draft is False
    raw = client.get.side_effect(f"{REPO}/pullrequests/42")
    raw["draft"] = True
    adapter._pr = None
    assert adapter.pull_request().draft is True
