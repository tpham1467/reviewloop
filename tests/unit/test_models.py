from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from reviewloop.agents.base import AgentAdapter, AgentResult, AgentTask
from reviewloop.errors import AgentStepError, ConfigError, ReviewloopError
from reviewloop.models import (
    Anchor,
    Command,
    Event,
    EventKind,
    Finding,
    FindingMarker,
    PullRequest,
    Reply,
    RunState,
    Severity,
    Side,
    Thread,
    ThreadRef,
    User,
)
from reviewloop.trackers.base import Ticket, TrackerAdapter
from reviewloop.vcs.base import VcsAdapter


def _finding(**kw: object) -> Finding:
    data: dict[str, object] = {
        "path": "a.py",
        "line": 3,
        "severity": "high",
        "category": "bug",
        "text": "t",
        "existing_code": "x = 1",
    }
    data.update(kw)
    return Finding.model_validate(data)


def test_finding_rejects_unknown_severity() -> None:
    with pytest.raises(ValidationError):
        _finding(severity="blocker")


def test_finding_defaults_and_round_trip() -> None:
    f = _finding()
    assert f.side is Side.NEW and f.id is None and f.security is False
    assert Finding.model_validate(f.model_dump()) == f
    assert f.severity is Severity.HIGH


def test_run_state_requires_v1() -> None:
    RunState(v=1, sha="a", base="b", turn=1, open=["f-abcd"])
    with pytest.raises(ValidationError):
        RunState.model_validate({"v": 2, "sha": "a", "base": "b", "turn": 1, "open": []})


def test_event_ignored_has_no_command() -> None:
    e = Event(kind=EventKind.IGNORED)
    assert e.command is None and e.actor is None and e.pr_number is None
    assert Event(kind=EventKind.COMMAND, command=Command.REVIEW).command is Command.REVIEW


def test_thread_with_marker() -> None:
    user = User(id="1", username="u")
    marker = FindingMarker(v=1, id="f-abcd", fp="abcdef", sev=Severity.LOW, rule=None)
    t = Thread(
        ref=ThreadRef(id="t1"),
        path=None,
        line=None,
        body="b",
        resolved=False,
        replies=[Reply(author=user, body="r", created_at=datetime.now(UTC))],
        marker=marker,
    )
    assert t.marker is marker


def test_errors_hierarchy() -> None:
    assert issubclass(AgentStepError, ReviewloopError) and issubclass(ConfigError, ReviewloopError)


class _Out(BaseModel):
    ok: bool


class _FakeVcs:
    def event(self) -> Event:
        return Event(kind=EventKind.IGNORED)

    def pull_request(self) -> PullRequest:
        u = User(id="1", username="u")
        return PullRequest(
            number=1, title="t", body="", source_branch="s", target_branch="m",
            head_sha="h", base_sha="b", author=u, is_fork=False, web_url="http://x",
        )  # fmt: skip

    def bot_identity(self) -> User:
        return User(id="2", username="bot")

    def can_write(self, user: User) -> bool:
        return True

    def threads(self) -> list[Thread]:
        return []

    def post_inline(self, finding: Finding, anchor: Anchor) -> ThreadRef:
        return ThreadRef(id="1")

    def post_general(self, body: str) -> ThreadRef:
        return ThreadRef(id="1")

    def reply(self, thread: ThreadRef, body: str) -> None:
        return None

    def resolve(self, thread: ThreadRef) -> None:
        return None

    def upsert_summary(self, body: str) -> None:
        return None

    def suggestion_block(self, anchor: Anchor, code: str) -> str | None:
        return None

    def read_file(self, path: str, ref: str) -> str | None:
        return None


class _FakeAgent:
    name = "fake"

    def check_auth(self) -> None:
        return None

    def run(self, task: AgentTask) -> AgentResult:
        return AgentResult(output=_Out(ok=True), transcript_path=Path("t"), duration_s=0.1)


class _FakeTracker:
    def fetch(self, keys: list[str]) -> list[Ticket]:
        return [Ticket(key=k, title="t", body="b", url="u") for k in keys]


def test_fakes_satisfy_protocols() -> None:
    vcs: VcsAdapter = _FakeVcs()
    agent: AgentAdapter = _FakeAgent()
    tracker: TrackerAdapter = _FakeTracker()
    assert vcs.pull_request().number == 1
    task = AgentTask(step="s", prompt="p", skills=[], workdir=Path("."), output_schema=_Out, timeout_s=5)
    assert agent.run(task).output == _Out(ok=True)
    assert tracker.fetch(["A-1"])[0].key == "A-1"
    assert vcs.event().kind is EventKind.IGNORED
    assert vcs.bot_identity().username == "bot" and vcs.can_write(User(id="1", username="u"))
    anchor = Anchor(path="a", line=1, side=Side.NEW, in_diff=True)
    assert vcs.post_inline(_finding(), anchor).id == "1" and vcs.post_general("x").id == "1"
    assert vcs.threads() == [] and vcs.suggestion_block(anchor, "c") is None and vcs.read_file("a", "r") is None
    vcs.reply(ThreadRef(id="1"), "b")
    vcs.resolve(ThreadRef(id="1"))
    vcs.upsert_summary("b")
    agent.check_auth()
