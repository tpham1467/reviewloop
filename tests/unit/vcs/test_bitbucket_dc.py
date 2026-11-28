from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
import requests
from requests import HTTPError, Response

from reviewloop.errors import ReviewloopError
from reviewloop.models import Anchor, Command, EventKind, Finding, Severity, Side, ThreadRef, User
from reviewloop.vcs.bitbucket_dc import SUMMARY_MARKER, BitbucketDCAdapter, clean_text

FAKE_TOKEN = "fake-" + "token"
ENV = {"BB_URL": "https://bb.example.com", "BB_PROJECT": "PRJ", "BB_REPO": "repo", "BB_PR_ID": "7"}
BOT = {"slug": "bot", "name": "bot"}
ALICE = {"slug": "alice", "name": "alice"}
PR = "rest/api/1.0/projects/PRJ/repos/repo/pull-requests/7"


def http_error(status: int) -> HTTPError:
    response = Response()
    response.status_code = status
    return HTTPError("boom", response=response)


class Fake:
    """Routes GET by path; records writes."""

    def __init__(self) -> None:
        self.routes: dict[str, Any] = {}
        self.get_calls: list[tuple[str, dict[str, Any]]] = []
        self.post = MagicMock(return_value={"id": 500})
        self.put = MagicMock(return_value={})

    def get(self, path: str, **kwargs: Any) -> Any:
        self.get_calls.append((path, kwargs))
        value = self.routes.get(path)
        if isinstance(value, Exception):
            raise value
        if kwargs.get("advanced_mode"):
            return SimpleNamespace(headers={"X-AUSERNAME": "bot"})
        if value is None:
            return {"values": [], "isLastPage": True}
        return value


def page(values: list[dict[str, Any]], last: bool = True, nxt: int = 0) -> dict[str, Any]:
    return {"values": values, "isLastPage": last, "nextPageStart": nxt}


def commented(comment: dict[str, Any]) -> dict[str, Any]:
    return {"action": "COMMENTED", "comment": comment}


def make(env: dict[str, str] | None = None) -> tuple[BitbucketDCAdapter, Fake]:
    fake = Fake()
    fake.routes["rest/api/1.0/users/bot"] = BOT
    adapter = BitbucketDCAdapter(ENV["BB_URL"], FAKE_TOKEN, "PRJ", "repo", {**ENV, **(env or {})}, client=fake)
    return adapter, fake


def finding(**kw: Any) -> Finding:
    base: dict[str, Any] = {
        "path": "a.py", "line": 5, "severity": Severity.HIGH, "category": "bug",
        "text": "problem here", "existing_code": "x", "suggestion": "y = 1",
    }  # fmt: skip
    return Finding(**{**base, **kw})


def test_from_env_names_missing_variable() -> None:
    with pytest.raises(ReviewloopError, match="REVIEWLOOP_BITBUCKET_TOKEN"):
        BitbucketDCAdapter.from_env(ENV)


def test_from_env_reads_token_and_url() -> None:
    env = {**ENV, "REVIEWLOOP_BITBUCKET_TOKEN": FAKE_TOKEN}
    assert isinstance(BitbucketDCAdapter.from_env(env), BitbucketDCAdapter)


def test_event_opened() -> None:
    adapter, _ = make({"BB_EVENT": "pr:opened"})
    event = adapter.event()
    assert event.kind is EventKind.OPENED and event.pr_number == 7


def test_event_opened_with_summary_is_ignored() -> None:
    adapter, fake = make({"BB_EVENT": "pr:opened"})
    fake.routes[f"{PR}/activities"] = page([commented({"id": 1, "text": f"x\n{SUMMARY_MARKER}", "author": BOT})])
    assert adapter.event().kind is EventKind.IGNORED


def test_event_summary_marker_from_other_author_does_not_skip() -> None:
    adapter, fake = make({"BB_EVENT": "pr:opened"})
    fake.routes[f"{PR}/activities"] = page([commented({"id": 1, "text": SUMMARY_MARKER, "author": ALICE})])
    assert adapter.event().kind is EventKind.OPENED


def test_event_command() -> None:
    adapter, _ = make({"BB_EVENT": "pr:comment:added", "BB_COMMENT": "/reviewloop recheck", "BB_ACTOR": "alice"})
    event = adapter.event()
    assert event.kind is EventKind.COMMAND and event.command is Command.RECHECK
    assert event.actor == User(id="alice", username="alice") and event.pr_number == 7


@pytest.mark.parametrize(
    "env",
    [
        {"BB_EVENT": "pr:merged"},
        {"BB_EVENT": ""},
        {"BB_EVENT": "pr:comment:added", "BB_COMMENT": "looks good", "BB_ACTOR": "alice"},
        {"BB_EVENT": "pr:comment:added", "BB_COMMENT": "/reviewloop nope", "BB_ACTOR": "alice"},
        {"BB_EVENT": "pr:comment:added", "BB_COMMENT": "/reviewloop review", "BB_ACTOR": ""},
        {"BB_EVENT": "pr:comment:added", "BB_COMMENT": "", "BB_ACTOR": "alice"},
        {"BB_EVENT": "pr:opened", "BB_PR_ID": "abc"},
        {"BB_EVENT": "pr:opened", "BB_PR_ID": ""},
    ],
)
def test_event_ignored(env: dict[str, str]) -> None:
    adapter, _ = make(env)
    assert adapter.event().kind is EventKind.IGNORED


def test_pull_request_maps_fields() -> None:
    adapter, fake = make()
    fake.routes[PR] = {
        "id": 7, "title": "T", "description": None,
        "fromRef": {"displayId": "feat", "latestCommit": "h1", "repository": {"id": 1}},
        "toRef": {"displayId": "main", "latestCommit": "b1", "repository": {"id": 1}},
    }  # fmt: skip
    pr = adapter.pull_request()
    assert (pr.number, pr.body, pr.source_branch, pr.target_branch) == (7, "", "feat", "main")
    assert (pr.head_sha, pr.base_sha, pr.is_fork) == ("h1", "b1", False)


def test_pull_request_fork_and_bad_shape() -> None:
    adapter, fake = make()
    fake.routes[PR] = {
        "id": 7, "title": "T",
        "fromRef": {"displayId": "f", "latestCommit": "h", "repository": {"id": 2}},
        "toRef": {"displayId": "m", "latestCommit": "b", "repository": {"id": 1}},
        "author": {"user": ALICE},
    }  # fmt: skip
    assert adapter.pull_request().is_fork is True
    fake.routes[PR] = {"id": 7}
    with pytest.raises(ReviewloopError):
        adapter.pull_request()


def test_pr_id_missing_raises() -> None:
    adapter, _ = make()
    adapter._env = {}
    with pytest.raises(ReviewloopError):
        adapter.pull_request()


def test_bot_identity_and_missing_header() -> None:
    adapter, _ = make()
    assert adapter.bot_identity() == User(id="bot", username="bot")
    adapter2, fake = make()
    fake.get = lambda path, **kw: SimpleNamespace(headers={})  # type: ignore[method-assign]
    with pytest.raises(ReviewloopError):
        adapter2.bot_identity()


def test_can_write_repo_permission() -> None:
    adapter, fake = make()
    fake.routes["rest/api/1.0/projects/PRJ/repos/repo/permissions/users"] = page(
        [{"user": ALICE, "permission": "REPO_WRITE"}]
    )
    assert adapter.can_write(User(id="alice", username="alice")) is True


def test_can_write_project_permission() -> None:
    adapter, fake = make()
    fake.routes["rest/api/1.0/projects/PRJ/permissions/users"] = page([{"user": ALICE, "permission": "PROJECT_ADMIN"}])
    assert adapter.can_write(User(id="alice", username="alice")) is True


def test_can_write_read_only_is_false() -> None:
    adapter, fake = make()
    fake.routes["rest/api/1.0/projects/PRJ/repos/repo/permissions/users"] = page(
        [{"user": ALICE, "permission": "REPO_READ"}]
    )
    assert adapter.can_write(User(id="alice", username="alice")) is False


def test_can_write_404_false_and_500_raises() -> None:
    adapter, fake = make()
    fake.routes["rest/api/1.0/projects/PRJ/repos/repo/permissions/users"] = http_error(404)
    assert adapter.can_write(User(id="alice", username="alice")) is False
    fake.routes["rest/api/1.0/projects/PRJ/repos/repo/permissions/users"] = http_error(500)
    with pytest.raises(ReviewloopError) as info:
        adapter.can_write(User(id="alice", username="alice"))
    assert "500" in str(info.value) and FAKE_TOKEN not in str(info.value)


def test_threads_filters_bot_reads_resolved_replies_and_pages() -> None:
    adapter, fake = make()
    marker = '<!-- reviewloop:finding {"v":1,"id":"f1","fp":"abc","sev":"high","rule":null} -->'
    first = commented({
        "id": 10, "text": f"body\n{marker}", "author": BOT, "threadResolved": True,
        "anchor": {"path": "a.py", "line": 5},
        "comments": [{
            "id": 11, "text": "why?", "author": ALICE, "createdDate": 1_700_000_000_000,
            "comments": [{"id": 12, "text": "nested", "author": BOT, "createdDate": 1_700_000_001_000}],
        }],
    })  # fmt: skip
    other = commented({"id": 20, "text": "hi", "author": ALICE})
    fake.routes[f"{PR}/activities"] = page([first], last=False, nxt=1)
    original = fake.get

    def paged(path: str, **kw: Any) -> Any:
        if path == f"{PR}/activities" and kw["params"]["start"] == 1:
            return page([other, {"action": "OPENED"}])
        return original(path, **kw)

    fake.get = paged  # type: ignore[method-assign]
    threads = adapter.threads()
    assert len(threads) == 1
    t = threads[0]
    assert t.ref == ThreadRef(id="10", note_id="10") and (t.path, t.line, t.resolved) == ("a.py", 5, True)
    assert t.side is Side.NEW
    assert [r.body for r in t.replies] == ["why?", "nested"]
    assert t.marker is not None and t.marker.id == "f1"


def test_post_inline_new_and_old_anchor() -> None:
    adapter, fake = make()
    ref = adapter.post_inline(finding(), Anchor(path="a.py", line=5, side=Side.NEW, in_diff=True))
    assert ref == ThreadRef(id="500", note_id="500")
    path = fake.post.call_args.args[0]
    data = fake.post.call_args.kwargs["data"]
    assert path == f"{PR}/comments"
    assert data["anchor"] == {"path": "a.py", "line": 5, "lineType": "ADDED", "fileType": "TO"}
    assert data["text"] == "problem here"
    adapter.post_inline(finding(), Anchor(path="a.py", line=3, side=Side.OLD, in_diff=True))
    anchor = fake.post.call_args.kwargs["data"]["anchor"]
    assert (anchor["lineType"], anchor["fileType"]) == ("REMOVED", "FROM")


def test_post_inline_never_includes_suggestion() -> None:
    adapter, fake = make()
    adapter.post_inline(finding(), Anchor(path="a.py", line=5, side=Side.NEW, in_diff=True, start_line=3))
    assert "suggestion" not in fake.post.call_args.kwargs["data"]["text"]


def test_post_inline_out_of_diff_is_general_with_location() -> None:
    adapter, fake = make()
    adapter.post_inline(finding(), Anchor(path="a.py", line=9, side=Side.NEW, in_diff=False, start_line=4))
    data = fake.post.call_args.kwargs["data"]
    assert "anchor" not in data and data["text"] == "`a.py:4-9`\n\nproblem here"


def test_post_inline_falls_back_on_rejection() -> None:
    adapter, fake = make()
    fake.post.side_effect = [http_error(400), http_error(400), {"id": 600}]
    ref = adapter.post_inline(finding(), Anchor(path="a.py", line=5, side=Side.NEW, in_diff=True))
    assert ref.id == "600"
    assert fake.post.call_args.kwargs["data"]["text"] == "`a.py:5`\n\nproblem here"


def test_post_inline_new_side_retries_as_context() -> None:
    adapter, fake = make()
    fake.post.side_effect = [http_error(409), {"id": 700}]
    ref = adapter.post_inline(finding(), Anchor(path="a.py", line=5, side=Side.NEW, in_diff=True))
    assert ref.id == "700" and fake.post.call_count == 2
    first, second = (c.kwargs["data"]["anchor"] for c in fake.post.call_args_list)
    assert first["lineType"] == "ADDED"
    assert (second["lineType"], second["fileType"]) == ("CONTEXT", "TO")


def test_post_inline_context_rejected_falls_back_to_general() -> None:
    adapter, fake = make()
    fake.post.side_effect = [http_error(400), http_error(400), {"id": 800}]
    ref = adapter.post_inline(finding(), Anchor(path="a.py", line=5, side=Side.NEW, in_diff=True))
    assert ref.id == "800" and fake.post.call_count == 3
    assert "anchor" not in fake.post.call_args.kwargs["data"]


def test_post_inline_old_side_has_no_context_retry() -> None:
    adapter, fake = make()
    fake.post.side_effect = [http_error(400), {"id": 900}]
    adapter.post_inline(finding(), Anchor(path="a.py", line=5, side=Side.OLD, in_diff=True))
    assert fake.post.call_count == 2 and "anchor" not in fake.post.call_args.kwargs["data"]


def test_network_error_is_wrapped() -> None:
    adapter, fake = make()
    fake.post.side_effect = requests.ConnectionError("down")
    with pytest.raises(ReviewloopError, match="network error"):
        adapter.post_general("x")


def test_paged_stops_without_next_page_start() -> None:
    adapter, fake = make({"BB_EVENT": "pr:opened"})
    fake.routes[f"{PR}/activities"] = {"values": [], "isLastPage": False}
    assert adapter.event().kind is EventKind.OPENED


def test_resolve_other_failure_has_no_version_hint() -> None:
    adapter, fake = make()
    fake.routes[f"{PR}/comments/10"] = {"id": 10, "version": 3}
    fake.put.side_effect = http_error(500)
    with pytest.raises(ReviewloopError) as info:
        adapter.resolve(ThreadRef(id="10"))
    assert "500" in str(info.value) and "8.0" not in str(info.value)


def test_post_inline_server_error_raises() -> None:
    adapter, fake = make()
    fake.post.side_effect = http_error(500)
    with pytest.raises(ReviewloopError):
        adapter.post_inline(finding(), Anchor(path="a.py", line=5, side=Side.NEW, in_diff=True))
    assert fake.post.call_count == 1


def test_post_general_and_reply() -> None:
    adapter, fake = make()
    assert adapter.post_general("hello").id == "500"
    adapter.reply(ThreadRef(id="10"), "answer")
    assert fake.post.call_args.kwargs["data"] == {"text": "answer", "parent": {"id": 10}}


def test_resolve_puts_thread_resolved_with_version() -> None:
    adapter, fake = make()
    fake.routes[f"{PR}/comments/10"] = {"id": 10, "version": 3}
    adapter.resolve(ThreadRef(id="10"))
    assert fake.put.call_args.args[0] == f"{PR}/comments/10"
    assert fake.put.call_args.kwargs["data"] == {"version": 3, "threadResolved": True}


def test_resolve_rejected_raises_clearly() -> None:
    adapter, fake = make()
    fake.routes[f"{PR}/comments/10"] = {"id": 10, "version": 3}
    fake.put.side_effect = http_error(400)
    with pytest.raises(ReviewloopError, match=r"threadResolved requires Bitbucket Data Center 8.0 or later.*400"):
        adapter.resolve(ThreadRef(id="10"))


def test_upsert_summary_creates_with_marker() -> None:
    adapter, fake = make()
    adapter.upsert_summary("sum")
    assert fake.post.call_args.kwargs["data"]["text"] == f"sum\n{SUMMARY_MARKER}"


def test_upsert_summary_edits_existing() -> None:
    adapter, fake = make()
    fake.routes[f"{PR}/activities"] = page([commented({"id": 9, "text": SUMMARY_MARKER, "author": BOT})])
    fake.routes[f"{PR}/comments/9"] = {"id": 9, "version": 2}
    adapter.upsert_summary("new")
    assert not fake.post.called
    assert fake.put.call_args.kwargs["data"] == {"version": 2, "text": f"new\n{SUMMARY_MARKER}"}


def test_suggestion_block_is_none() -> None:
    adapter, _ = make()
    assert adapter.suggestion_block(Anchor(path="a", line=1, side=Side.NEW, in_diff=True), "x") is None


def test_read_file() -> None:
    adapter, fake = make()
    path = "rest/api/1.0/projects/PRJ/repos/repo/raw/src/a%20b.py"
    fake.routes[path] = b"print(1)\n"
    assert adapter.read_file("src/a b.py", "abc") == "print(1)\n"
    assert fake.get_calls[-1][1]["params"] == {"at": "abc"}
    fake.routes[path] = http_error(404)
    assert adapter.read_file("src/a b.py", "abc") is None
    fake.routes[path] = http_error(502)
    with pytest.raises(ReviewloopError, match="502"):
        adapter.read_file("src/a b.py", "abc")


def test_clean_text_replaces_4byte() -> None:
    assert clean_text("ok \U0001f600 é 中") == "ok � é 中"


def test_posted_text_has_no_4byte_chars() -> None:
    adapter, fake = make()
    emoji = "bad \U0001f600 text"
    adapter.post_general(emoji)
    adapter.post_inline(finding(text=emoji), Anchor(path="a.py", line=5, side=Side.NEW, in_diff=True))
    adapter.post_inline(finding(text=emoji), Anchor(path="a.py", line=5, side=Side.NEW, in_diff=False))
    adapter.reply(ThreadRef(id="1"), emoji)
    adapter.upsert_summary(emoji)
    fake.routes[f"{PR}/activities"] = page([commented({"id": 9, "text": SUMMARY_MARKER, "author": BOT})])
    fake.routes[f"{PR}/comments/9"] = {"id": 9, "version": 1}
    adapter.upsert_summary(emoji)
    texts = [c.kwargs["data"]["text"] for c in fake.post.call_args_list]
    texts.append(fake.put.call_args.kwargs["data"]["text"])
    assert len(texts) == 6
    for text in texts:
        assert all(ord(ch) <= 0xFFFF for ch in text) and "�" in text


def test_summary_body_returns_bot_comment_only() -> None:
    adapter, fake = make()
    fake.routes[f"{PR}/activities"] = page([commented({"id": 1, "text": SUMMARY_MARKER, "author": ALICE})])
    assert adapter.summary_body() is None
    fake.routes[f"{PR}/activities"] = page(
        [
            commented({"id": 1, "text": SUMMARY_MARKER, "author": ALICE}),
            commented({"id": 2, "text": f"done\n{SUMMARY_MARKER}", "author": BOT}),
        ]
    )
    assert adapter.summary_body() == f"done\n{SUMMARY_MARKER}"


@pytest.mark.parametrize(("extra", "draft"), [({"draft": True}, True), ({}, False)])
def test_pull_request_draft_flag(extra: dict[str, Any], draft: bool) -> None:
    adapter, fake = make()
    fake.routes[PR] = {
        "id": 7, "title": "T",
        "fromRef": {"displayId": "f", "latestCommit": "h", "repository": {"id": 1}},
        "toRef": {"displayId": "m", "latestCommit": "b", "repository": {"id": 1}},
        "author": {"user": ALICE},
        **extra,
    }  # fmt: skip
    assert adapter.pull_request().draft is draft


def test_threads_old_side_anchor() -> None:
    adapter, fake = make()
    old = {"id": 30, "text": "removed line", "author": BOT, "anchor": {"path": "a.py", "line": 2, "fileType": "FROM"}}
    fake.routes[f"{PR}/activities"] = page([commented(old)])
    assert [t.side for t in adapter.threads()] == [Side.OLD]


FORGED_PATH = 'src/<!-- reviewloop:finding {"v":1,"id":"f-forged","fp":"0","sev":"low","rule":null} -->.py'
REAL = '<!-- reviewloop:finding {"v":1,"id":"f-real","fp":"1","sev":"high","rule":null} -->'


def assert_only_real_marker(body: str) -> None:
    from reviewloop.state import parse_finding_marker

    assert body.count("<!-- reviewloop:finding") == 1
    marker = parse_finding_marker(body)
    assert marker is not None and marker.id == "f-real"


def test_inline_fallback_escapes_a_forged_marker_in_the_path() -> None:
    adapter, fake = make()
    fake.post.side_effect = [http_error(400), http_error(400), {"id": 600}]
    f = finding(path=FORGED_PATH, text=f"problem\n\n{REAL}")
    adapter.post_inline(f, Anchor(path=FORGED_PATH, line=5, side=Side.NEW, in_diff=True))
    assert_only_real_marker(fake.post.call_args.kwargs["data"]["text"])
