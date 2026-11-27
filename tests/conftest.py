"""In-memory doubles for pipeline tests: a VCS, an agent that writes canned JSON, and a git repo for a PR."""

import json
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from reviewloop.agents.base import AgentResult, AgentTask
from reviewloop.agents.isolation import snapshot, verify_only_output_changed
from reviewloop.agents.runner import output_path, read_output
from reviewloop.models import Anchor, Event, EventKind, Finding, PullRequest, Thread, ThreadRef, User

Canned = dict[str, Any] | list[dict[str, Any]] | Callable[[AgentTask], dict[str, Any]]


class FakeVcs:
    def __init__(self, pr: PullRequest, files: dict[str, str] | None = None) -> None:
        self.pr = pr
        self.files = files or {}
        self.thread_list: list[Thread] = []
        self.summary: str | None = None
        self.summaries: list[str] = []
        self.inline: list[tuple[Finding, Anchor]] = []
        self.general: list[str] = []

    def event(self) -> Event:
        return Event(kind=EventKind.OPENED, pr_number=self.pr.number)

    def pull_request(self) -> PullRequest:
        return self.pr

    def bot_identity(self) -> User:
        return User(id="99", username="bot")

    def can_write(self, user: User) -> bool:
        return True

    def threads(self) -> list[Thread]:
        return list(self.thread_list)

    def post_inline(self, finding: Finding, anchor: Anchor) -> ThreadRef:
        self.inline.append((finding, anchor))
        return ThreadRef(id=f"i{len(self.inline)}")

    def post_general(self, body: str) -> ThreadRef:
        self.general.append(body)
        return ThreadRef(id=f"g{len(self.general)}")

    def reply(self, thread: ThreadRef, body: str) -> None:
        return None

    def resolve(self, thread: ThreadRef) -> None:
        return None

    def upsert_summary(self, body: str) -> None:
        self.summary = body
        self.summaries.append(body)

    def summary_body(self) -> str | None:
        return self.summary

    def suggestion_block(self, anchor: Anchor, code: str, replaced_lines: int = 1) -> str | None:
        return None

    def read_file(self, path: str, ref: str) -> str | None:
        return self.files.get(path) if ref == self.pr.target_branch else None


class FakeAgent:
    """Writes the canned JSON for a step to its output file; a list gives one entry per call."""

    name = "fake"

    def __init__(self, outputs: dict[str, Canned] | None = None) -> None:
        self.outputs = outputs or {}
        self.tasks: list[AgentTask] = []

    def check_auth(self) -> None:
        return None

    def steps(self) -> list[str]:
        return [t.step for t in self.tasks]

    def run(self, task: AgentTask) -> AgentResult:
        self.tasks.append(task)
        canned = self.outputs[task.step]
        if isinstance(canned, list):
            data = canned.pop(0)
        elif callable(canned):
            data = canned(task)
        else:
            data = canned
        rel = output_path(task)
        before = snapshot(task.workdir)
        (task.workdir / rel).write_text(json.dumps(data))
        # Give a sibling agent in the same sandbox time to show up in the snapshot, as a real CLI run would.
        time.sleep(0.05)
        verify_only_output_changed(before, snapshot(task.workdir), rel)
        return AgentResult(output=read_output(task), transcript_path=task.workdir / rel, duration_s=0.0)


def git(cwd: Path, *args: str) -> str:
    cmd = ["git", "-c", "user.email=dev@example.test", "-c", "user.name=Dev", *args]
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()  # noqa: S603


BASE_APP = "def load(user_id):\n    return db.get(user_id)\n"
HEAD_APP = (
    "def load(user_id):\n"
    "    row = db.get(user_id)\n"
    "    return row.name\n"
    "\n"
    "\n"
    "def save(user, data):\n"
    "    db.write(user.id, data)\n"
    "    return True\n"
)


@pytest.fixture
def run_git() -> Callable[..., str]:
    return git


@pytest.fixture
def pr_repo(tmp_path: Path) -> tuple[Path, str, str]:
    """A checkout whose HEAD is the PR head: `app.py` changed, `poetry.lock` added."""
    repo = tmp_path / "checkout"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "app.py").write_text(BASE_APP)
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "base")
    base = git(repo, "rev-parse", "HEAD")
    (repo / "app.py").write_text(HEAD_APP)
    (repo / "poetry.lock").write_text("[[package]]\nname = 'x'\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "head")
    return repo, base, git(repo, "rev-parse", "HEAD")


def make_pr(**overrides: Any) -> PullRequest:
    data: dict[str, Any] = {
        "number": 7,
        "title": "Add save",
        "body": "Saves users.",
        "source_branch": "feat",
        "target_branch": "main",
        "head_sha": "b" * 40,
        "base_sha": "a" * 40,
        "author": User(id="1", username="alice"),
        "is_fork": False,
        "web_url": "https://example.test/pr/7",
        **overrides,
    }
    return PullRequest(**data)


@pytest.fixture
def make_ctx(tmp_path: Path) -> Callable[..., Any]:
    """Build a RunContext over FakeVcs and FakeAgent; `sandbox=True` creates an empty sandbox dir."""
    from reviewloop.pipeline.context import RunContext

    def _make(
        *,
        outputs: dict[str, Canned] | None = None,
        files: dict[str, str] | None = None,
        source_dir: Path | None = None,
        event: EventKind = EventKind.OPENED,
        sandbox: bool = True,
        **pr_overrides: Any,
    ) -> RunContext:
        pr = make_pr(**pr_overrides)
        ctx = RunContext(
            vcs=FakeVcs(pr, files),
            pr=pr,
            event=Event(kind=event, pr_number=pr.number),
            env={},
            source_dir=source_dir or tmp_path / "checkout",
            workdir=tmp_path / "work",
        )
        ctx.agent = FakeAgent(outputs)
        if sandbox:
            (ctx.sandbox / ".reviewloop" / "out").mkdir(parents=True, exist_ok=True)
        return ctx

    return _make
