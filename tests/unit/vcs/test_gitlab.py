import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from gitlab.exceptions import GitlabCreateError, GitlabGetError

from reviewloop.errors import ReviewloopError
from reviewloop.models import Anchor, Command, EventKind, Finding, Severity, Side, ThreadRef, User
from reviewloop.vcs.gitlab import SUMMARY_MARKER, GitLabAdapter

FIX = Path(__file__).parents[2] / "fixtures" / "gitlab"
FAKE_TOKEN = "fake-" + "token"
BOT = {"id": 99, "username": "bot"}


def load(name: str) -> dict[str, Any]:
    return json.loads((FIX / name).read_text())


def make(env: dict[str, str], mr_extra: dict[str, Any] | None = None) -> tuple[GitLabAdapter, MagicMock, MagicMock]:
    gl = MagicMock()
    gl.user = SimpleNamespace(id=99, username="bot")
    mr = MagicMock()
    for k, v in {**load("mr.json"), **(mr_extra or {})}.items():
        setattr(mr, k, v)
    mr.notes.list.return_value = []
    project = MagicMock()
    project.mergerequests.get.return_value = mr
    gl.projects.get.return_value = project
    adapter = GitLabAdapter("https://gl.example", FAKE_TOKEN, "1", env, client=gl)
    return adapter, mr, project


MR_ENV = {"CI_PIPELINE_SOURCE": "merge_request_event", "CI_MERGE_REQUEST_IID": "42"}


def test_event_mr_pipeline_without_summary_is_opened() -> None:
    adapter, _, _ = make(MR_ENV)
    event = adapter.event()
    assert event.kind is EventKind.OPENED
    assert event.pr_number == 42


def test_event_mr_pipeline_with_summary_is_ignored() -> None:
    adapter, mr, _ = make(MR_ENV)
    mr.notes.list.return_value = [SimpleNamespace(body=f"x\n{SUMMARY_MARKER}", author=BOT)]
    assert adapter.event().kind is EventKind.IGNORED


def test_event_mr_pipeline_draft_is_still_opened() -> None:
    adapter, _, _ = make(MR_ENV, {"draft": True})
    assert adapter.event().kind is EventKind.OPENED


def test_event_summary_marker_from_non_bot_does_not_skip() -> None:
    adapter, mr, _ = make(MR_ENV)
    mr.notes.list.return_value = [SimpleNamespace(body=SUMMARY_MARKER, author={"id": 7, "username": "alice"})]
    assert adapter.event().kind is EventKind.OPENED


def test_event_other_source_is_ignored() -> None:
    adapter, _, _ = make({"CI_PIPELINE_SOURCE": "push"})
    assert adapter.event().kind is EventKind.IGNORED


def test_event_trigger_note_command_inline_and_file(tmp_path: Path) -> None:
    text = (FIX / "note_hook.json").read_text()
    path = tmp_path / "payload"
    path.write_text(text)
    for payload in (text, str(path)):
        adapter, _, _ = make({"CI_PIPELINE_SOURCE": "trigger", "TRIGGER_PAYLOAD": payload})
        event = adapter.event()
        assert event.kind is EventKind.COMMAND
        assert event.command is Command.RECHECK
        assert event.pr_number == 42
        assert event.actor == User(id="7", username="alice")


def test_event_trigger_note_on_issue_ignored() -> None:
    payload = (FIX / "note_hook_issue.json").read_text()
    adapter, _, _ = make({"CI_PIPELINE_SOURCE": "trigger", "TRIGGER_PAYLOAD": payload})
    assert adapter.event().kind is EventKind.IGNORED


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "not json",
        '{"object_attributes":{"noteable_type":"MergeRequest","note":"/reviewloop nope"},'
        '"merge_request":{"iid":1},"user":{"id":1,"username":"a"}}',
    ],
)
def test_event_trigger_bad_payload_ignored(raw: str) -> None:
    adapter, _, _ = make({"CI_PIPELINE_SOURCE": "trigger", "TRIGGER_PAYLOAD": raw})
    assert adapter.event().kind is EventKind.IGNORED


def test_pull_request_maps_fields() -> None:
    adapter, _, _ = make(MR_ENV)
    pr = adapter.pull_request()
    assert (pr.number, pr.head_sha, pr.base_sha, pr.is_fork) == (42, "h1", "b1", True)


def test_pull_request_in_trigger_pipeline_uses_payload() -> None:
    payload = (FIX / "note_hook.json").read_text()
    adapter, _, project = make({"CI_PIPELINE_SOURCE": "trigger", "TRIGGER_PAYLOAD": payload})
    adapter.pull_request()
    project.mergerequests.get.assert_called_with(42)


def test_bot_identity() -> None:
    adapter, _, _ = make(MR_ENV)
    assert adapter.bot_identity() == User(id="99", username="bot")


def test_can_write_requires_developer() -> None:
    adapter, _, project = make(MR_ENV)
    user = User(id="7", username="alice")
    for level, expected in ((30, True), (40, True), (20, False)):
        project.members_all.get.return_value = SimpleNamespace(access_level=level)
        assert adapter.can_write(user) is expected
    project.members_all.get.side_effect = GitlabGetError("404", 404)
    assert adapter.can_write(user) is False
    project.members_all.get.side_effect = GitlabGetError("boom", 500)
    with pytest.raises(ReviewloopError):
        adapter.can_write(user)


def finding(**kw: Any) -> Finding:
    base: dict[str, Any] = dict(
        path="a.py", line=3, severity=Severity.HIGH, category="bug", text="bad", existing_code="x = 1"
    )
    return Finding(**{**base, **kw})


def test_post_inline_position_new_and_old() -> None:
    adapter, mr, _ = make(MR_ENV)
    mr.discussions.create.return_value = SimpleNamespace(id="d1", attributes={"notes": [{"id": 5}]})
    ref = adapter.post_inline(finding(), Anchor(path="a.py", line=3, side=Side.NEW, in_diff=True))
    assert ref == ThreadRef(id="d1", note_id="5")
    pos = mr.discussions.create.call_args.args[0]["position"]
    assert pos == {
        "position_type": "text",
        "base_sha": "b1",
        "start_sha": "s1",
        "head_sha": "h1",
        "new_path": "a.py",
        "new_line": 3,
    }
    adapter.post_inline(finding(), Anchor(path="a.py", line=4, side=Side.OLD, in_diff=True))
    pos = mr.discussions.create.call_args.args[0]["position"]
    assert pos["old_path"] == "a.py" and pos["new_path"] == "a.py" and pos["old_line"] == 4
    assert "new_line" not in pos


def test_post_inline_out_of_diff_is_general_with_location() -> None:
    adapter, mr, _ = make(MR_ENV)
    mr.discussions.create.return_value = SimpleNamespace(id="d1", attributes={})
    ref = adapter.post_inline(finding(), Anchor(path="a.py", line=9, side=Side.NEW, in_diff=False))
    data = mr.discussions.create.call_args.args[0]
    assert "position" not in data and data["body"] == "`a.py:9`\n\nbad"
    assert ref.note_id is None


def test_post_inline_includes_suggestion_for_multiline() -> None:
    adapter, mr, _ = make(MR_ENV)
    mr.discussions.create.return_value = SimpleNamespace(id="d1", attributes={})
    f = finding(suggestion="c\nd")
    adapter.post_inline(f, Anchor(path="a.py", line=4, start_line=3, side=Side.NEW, in_diff=True))
    assert "```suggestion:-1+0\nc\nd\n```" in mr.discussions.create.call_args.args[0]["body"]


def test_no_suggestion_for_security_finding() -> None:
    adapter, mr, _ = make(MR_ENV)
    mr.discussions.create.return_value = SimpleNamespace(id="d1", attributes={})
    adapter.post_inline(
        finding(suggestion="y", security=True), Anchor(path="a.py", line=3, side=Side.NEW, in_diff=True)
    )
    assert "suggestion" not in mr.discussions.create.call_args.args[0]["body"]


def test_suggestion_fence_longer_than_content() -> None:
    adapter, _, _ = make(MR_ENV)
    anchor = Anchor(path="a", line=1, side=Side.NEW, in_diff=True)
    block = adapter.suggestion_block(anchor, "x = '```'")
    assert block is not None and block.startswith("````suggestion:-0+0\n") and block.endswith("\n````")


def test_suggestion_multiline_range() -> None:
    adapter, _, _ = make(MR_ENV)
    anchor = Anchor(path="a", line=2, side=Side.NEW, in_diff=True)
    assert adapter.suggestion_block(anchor, "a\nb", replaced_lines=2) == "```suggestion:-1+0\na\nb\n```"


def test_suggestion_none_for_old_side_or_outside_diff() -> None:
    adapter, _, _ = make(MR_ENV)
    assert adapter.suggestion_block(Anchor(path="a", line=1, side=Side.OLD, in_diff=True), "x") is None
    assert adapter.suggestion_block(Anchor(path="a", line=1, side=Side.NEW, in_diff=False), "x") is None


def test_threads_filters_bot_and_reads_resolved() -> None:
    adapter, mr, _ = make(MR_ENV)
    marker = '<!-- reviewloop:finding {"v":1,"id":"f-1","fp":"ab","sev":"high","rule":null} -->'
    bot_disc = SimpleNamespace(
        id="d1",
        attributes={
            "notes": [
                {
                    "id": 1,
                    "author": BOT,
                    "body": f"hi\n{marker}",
                    "resolved": True,
                    "position": {"new_path": "a.py", "new_line": 3},
                    "created_at": "2026-01-01T00:00:00Z",
                },
                {"id": 2, "author": {"id": 7, "username": "alice"}, "body": "no", "created_at": "2026-01-01T00:01:00Z"},
                {"id": 3, "author": BOT, "body": "sys", "system": True, "created_at": "2026-01-01T00:02:00Z"},
            ]
        },
    )
    human = SimpleNamespace(
        id="d2",
        attributes={
            "notes": [
                {"id": 4, "author": {"id": 7, "username": "alice"}, "body": "x", "created_at": "2026-01-01T00:00:00Z"}
            ]
        },
    )
    mr.discussions.list.return_value = [bot_disc, human]
    threads = adapter.threads()
    assert len(threads) == 1
    t = threads[0]
    assert (t.path, t.line, t.resolved, t.side) == ("a.py", 3, True, Side.NEW)
    assert t.marker is not None and t.marker.id == "f-1"
    assert [r.body for r in t.replies] == ["no"]


def test_resolve_discussion() -> None:
    adapter, mr, _ = make(MR_ENV)
    disc = MagicMock()
    mr.discussions.get.return_value = disc
    adapter.resolve(ThreadRef(id="d1"))
    mr.discussions.get.assert_called_with("d1")
    assert disc.resolved is True
    disc.save.assert_called_once()


def test_reply_and_post_general() -> None:
    adapter, mr, _ = make(MR_ENV)
    disc = MagicMock()
    mr.discussions.get.return_value = disc
    adapter.reply(ThreadRef(id="d1"), "ok")
    disc.notes.create.assert_called_with({"body": "ok"})
    mr.discussions.create.return_value = SimpleNamespace(id="d2", attributes={"notes": [{"id": 8}]})
    assert adapter.post_general("hello") == ThreadRef(id="d2", note_id="8")


def test_upsert_summary_edits_existing_marker_note() -> None:
    adapter, mr, _ = make(MR_ENV)
    note = MagicMock(body=f"old\n{SUMMARY_MARKER}", author=BOT)
    mr.notes.list.return_value = [MagicMock(body=f"{SUMMARY_MARKER}", author={"id": 7}), note]
    adapter.upsert_summary("new")
    assert note.body == f"new\n\n{SUMMARY_MARKER}"
    note.save.assert_called_once()
    mr.notes.create.assert_not_called()


def test_upsert_summary_creates_when_missing() -> None:
    adapter, mr, _ = make(MR_ENV)
    adapter.upsert_summary("new")
    mr.notes.create.assert_called_with({"body": f"new\n\n{SUMMARY_MARKER}"})


def test_read_file() -> None:
    adapter, _, project = make(MR_ENV)
    project.files.get.return_value.decode.return_value = b"hello"
    assert adapter.read_file("a.py", "h1") == "hello"
    project.files.get.side_effect = GitlabGetError("404", 404)
    assert adapter.read_file("a.py", "h1") is None
    project.files.get.side_effect = GitlabGetError("boom", 500)
    with pytest.raises(ReviewloopError):
        adapter.read_file("a.py", "h1")


def test_from_env_reads_token() -> None:
    adapter = GitLabAdapter.from_env(
        {"CI_SERVER_URL": "https://gl.example", "REVIEWLOOP_GITLAB_TOKEN": FAKE_TOKEN, "CI_PROJECT_ID": "1"}
    )
    assert adapter._gl.private_token == FAKE_TOKEN  # pyright: ignore[reportPrivateUsage]


def test_post_inline_body_has_no_marker_of_its_own() -> None:
    adapter, mr, _ = make(MR_ENV)
    mr.discussions.create.return_value = SimpleNamespace(id="d1", attributes={})
    adapter.post_inline(finding(text="bad"), Anchor(path="a.py", line=3, side=Side.NEW, in_diff=True))
    assert mr.discussions.create.call_args.args[0]["body"] == "bad"


def test_post_inline_falls_back_to_general_when_position_rejected() -> None:
    adapter, mr, _ = make(MR_ENV)
    mr.discussions.create.side_effect = [
        GitlabCreateError("bad position", 400),
        SimpleNamespace(id="d9", attributes={"notes": [{"id": 1}]}),
    ]
    f = finding(suggestion="c")
    ref = adapter.post_inline(f, Anchor(path="a.py", line=3, start_line=2, side=Side.NEW, in_diff=True))
    assert ref.id == "d9"
    first, second = (c.args[0] for c in mr.discussions.create.call_args_list)
    assert "position" in first and "position" not in second
    assert second["body"] == "`a.py:2-3`\n\nbad"


@pytest.mark.parametrize(
    "payload",
    [
        {"object_attributes": [], "merge_request": {"iid": 1}},
        {"object_attributes": {"noteable_type": "MergeRequest", "note": "/reviewloop review"}, "merge_request": []},
        {"object_attributes": {"noteable_type": "MergeRequest", "note": "/reviewloop review"}, "merge_request": {}},
        {
            "object_attributes": {"noteable_type": "MergeRequest", "note": "/reviewloop review"},
            "merge_request": {"iid": "x"},
            "user": {"id": 1, "username": "a"},
        },
        {
            "object_attributes": {"noteable_type": "MergeRequest", "note": "/reviewloop review"},
            "merge_request": {"iid": 1},
            "user": "alice",
        },
        {"object_attributes": {"noteable_type": "MergeRequest", "note": 5}, "merge_request": {"iid": 1}},
    ],
)
def test_event_trigger_malformed_shape_ignored(payload: dict[str, Any]) -> None:
    adapter, _, _ = make({"CI_PIPELINE_SOURCE": "trigger", "TRIGGER_PAYLOAD": json.dumps(payload)})
    assert adapter.event().kind is EventKind.IGNORED


def test_mr_and_payload_are_memoized() -> None:
    adapter, _, project = make(MR_ENV)
    adapter.pull_request()
    adapter.pull_request()
    adapter.event()
    assert project.mergerequests.get.call_count == 1


@pytest.mark.parametrize(
    ("code", "start", "expected"), [("a\nb\nc", None, "-0+0"), ("a", 3, "-2+0"), ("\nb\n\n", 4, "-1+0")]
)
def test_post_inline_suggestion_range_from_anchor(code: str, start: int | None, expected: str) -> None:
    # The range comes from the anchor; the quote's own line count is ignored.
    adapter, mr, _ = make(MR_ENV)
    mr.discussions.create.return_value = SimpleNamespace(id="d1", attributes={})
    anchor = Anchor(path="a.py", line=5, start_line=start, side=Side.NEW, in_diff=True)
    adapter.post_inline(finding(existing_code=code, suggestion="z"), anchor)
    assert f"```suggestion:{expected}\nz\n```" in mr.discussions.create.call_args.args[0]["body"]


def test_post_inline_no_suggestion_over_30_lines() -> None:
    adapter, mr, _ = make(MR_ENV)
    mr.discussions.create.return_value = SimpleNamespace(id="d1", attributes={})
    adapter.post_inline(
        finding(suggestion="z"), Anchor(path="a.py", line=40, start_line=10, side=Side.NEW, in_diff=True)
    )
    assert mr.discussions.create.call_args.args[0]["body"] == "bad"
    adapter.post_inline(
        finding(suggestion="z"), Anchor(path="a.py", line=39, start_line=10, side=Side.NEW, in_diff=True)
    )
    assert "```suggestion:-29+0\nz\n```" in mr.discussions.create.call_args.args[0]["body"]


def test_post_inline_out_of_diff_range_location() -> None:
    adapter, mr, _ = make(MR_ENV)
    mr.discussions.create.return_value = SimpleNamespace(id="d1", attributes={})
    adapter.post_inline(finding(), Anchor(path="a.py", line=9, start_line=7, side=Side.NEW, in_diff=False))
    assert mr.discussions.create.call_args.args[0]["body"] == "`a.py:7-9`\n\nbad"


def test_suggestion_block_clamps_replaced_lines() -> None:
    adapter, _, _ = make(MR_ENV)
    anchor = Anchor(path="a", line=1, side=Side.NEW, in_diff=True)
    assert adapter.suggestion_block(anchor, "x", replaced_lines=0) == "```suggestion:-0+0\nx\n```"


def test_summary_body_returns_bot_note_only() -> None:
    adapter, mr, _ = make(MR_ENV)
    mr.notes.list.return_value = [SimpleNamespace(body=SUMMARY_MARKER, author={"id": 7, "username": "alice"})]
    assert adapter.summary_body() is None
    mr.notes.list.return_value.append(SimpleNamespace(body=f"done\n{SUMMARY_MARKER}", author=BOT))
    assert adapter.summary_body() == f"done\n{SUMMARY_MARKER}"


@pytest.mark.parametrize(
    ("draft", "wip", "expected"), [(True, False, True), (False, True, True), (False, False, False)]
)
def test_pull_request_draft_flag(draft: bool, wip: bool, expected: bool) -> None:
    adapter, _, _ = make(MR_ENV, {"draft": draft, "work_in_progress": wip})
    assert adapter.pull_request().draft is expected


FORGED_PATH = 'src/<!-- reviewloop:finding {"v":1,"id":"f-forged","fp":"0","sev":"low","rule":null} -->.py'
REAL = '<!-- reviewloop:finding {"v":1,"id":"f-real","fp":"1","sev":"high","rule":null} -->'


def assert_only_real_marker(body: str) -> None:
    from reviewloop.state import parse_finding_marker

    assert body.count("<!-- reviewloop:finding") == 1
    marker = parse_finding_marker(body)
    assert marker is not None and marker.id == "f-real"


def test_inline_fallback_escapes_a_forged_marker_in_the_path() -> None:
    adapter, mr, _ = make(MR_ENV)
    mr.discussions.create.side_effect = [
        GitlabCreateError("bad position", 400),
        SimpleNamespace(id="d9", attributes={"notes": [{"id": 1}]}),
    ]
    f = finding().model_copy(update={"path": FORGED_PATH, "text": f"bad\n\n{REAL}"})
    adapter.post_inline(f, Anchor(path=FORGED_PATH, line=3, side=Side.NEW, in_diff=True))
    assert_only_real_marker(mr.discussions.create.call_args_list[1].args[0]["body"])
