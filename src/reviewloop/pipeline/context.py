"""Shared state of one pipeline run."""

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from reviewloop.agents.base import AgentAdapter
from reviewloop.config import Config
from reviewloop.diff import FileDiff
from reviewloop.models import CoverageStatus, Event, FileCoverage, Finding, PullRequest, RunState, Thread
from reviewloop.state import parse_state_marker
from reviewloop.trackers.base import Ticket, TrackerAdapter
from reviewloop.vcs.base import VcsAdapter


@dataclass
class RunContext:
    vcs: VcsAdapter
    pr: PullRequest
    event: Event
    env: Mapping[str, str]
    # The CI checkout of the PR head; never handed to an agent.
    source_dir: Path
    workdir: Path
    config: Config = field(default_factory=Config)
    # Built by load-config from the config when the caller does not pass one.
    agent: AgentAdapter | None = None
    tracker: TrackerAdapter | None = None
    sandbox: Path = field(init=False)
    diffs: list[FileDiff] = field(default_factory=list[FileDiff])
    excluded: list[FileCoverage] = field(default_factory=list[FileCoverage])
    previous_threads: list[Thread] = field(default_factory=list[Thread])
    previous_state: RunState | None = None
    # Turn in the state marker when this run started, and the turn this run writes. turn == 0: not read yet.
    start_turn: int = 0
    turn: int = 0
    state: RunState | None = None
    tickets: list[Ticket] = field(default_factory=list[Ticket])
    context_summary: str = ""
    stacks: set[str] = field(default_factory=set[str])
    findings: list[Finding] = field(default_factory=list[Finding])
    security_findings: list[Finding] = field(default_factory=list[Finding])
    coverage: list[FileCoverage] = field(default_factory=list[FileCoverage])
    overview: str = ""
    posted: list[Finding] = field(default_factory=list[Finding])
    log: logging.Logger = field(default_factory=lambda: logging.getLogger("reviewloop.pipeline"))

    def __post_init__(self) -> None:
        self.sandbox = self.workdir / "sandbox"

    def reviewable(self) -> list[FileDiff]:
        """Diff files that go to the agent: every file not excluded."""
        skipped = {c.path for c in self.excluded}
        return [d for d in self.diffs if d.path not in skipped]

    def fail_uncovered(self, reason: str) -> None:
        """Give every diff file that has no coverage entry yet a `failed` entry (spec 6.4: none goes unlisted)."""
        covered = {c.path for c in self.coverage}
        self.coverage += [
            FileCoverage(path=d.path, status=CoverageStatus.FAILED, reason=reason)
            for d in self.diffs
            if d.path not in covered
        ]

    def current_state(self) -> RunState | None:
        """The run-state marker in the bot's summary comment right now."""
        return parse_state_marker(self.vcs.summary_body() or "")

    def fresh_state(self) -> RunState:
        return RunState(v=1, sha=self.pr.head_sha, base=self.pr.base_sha, turn=0, open=[])

    def overtaken(self, current: RunState | None) -> bool:
        """Whether another run advanced the turn after this run read it (spec 6.1, overlapping runs)."""
        return self.turn > 0 and current is not None and current.turn > self.start_turn
