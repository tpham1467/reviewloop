from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Side(StrEnum):
    NEW = "new"
    OLD = "old"


class Finding(BaseModel):
    id: str | None = None
    path: str
    line: int
    side: Side = Side.NEW
    severity: Severity
    rule: str | None = None
    category: str
    text: str
    existing_code: str
    cause_code: str | None = None
    suggestion: str | None = None
    security: bool = False


class Anchor(BaseModel):
    path: str
    line: int
    side: Side
    in_diff: bool
    # First line of a multi-line range ending at `line`; None means a single line.
    start_line: int | None = None


class User(BaseModel):
    id: str
    username: str


class PullRequest(BaseModel):
    number: int
    title: str
    body: str
    source_branch: str
    target_branch: str
    head_sha: str
    base_sha: str
    author: User
    is_fork: bool
    web_url: str


class ThreadRef(BaseModel):
    id: str
    note_id: str | None = None


class Reply(BaseModel):
    author: User
    body: str
    created_at: datetime


class FindingMarker(BaseModel):
    v: Literal[1]
    id: str
    fp: str
    sev: Severity
    rule: str | None


class Thread(BaseModel):
    ref: ThreadRef
    path: str | None
    line: int | None
    body: str
    resolved: bool
    replies: list[Reply]
    marker: FindingMarker | None


class RunState(BaseModel):
    v: Literal[1]
    sha: str
    base: str
    turn: int
    open: list[str]


class EventKind(StrEnum):
    OPENED = "opened"
    COMMAND = "command"
    IGNORED = "ignored"


class Command(StrEnum):
    REVIEW = "review"
    RECHECK = "recheck"
    HELP = "help"


class Event(BaseModel):
    kind: EventKind
    command: Command | None = None
    actor: User | None = None
    pr_number: int | None = None


class CoverageStatus(StrEnum):
    REVIEWED = "reviewed"
    FAILED = "failed"
    EXCLUDED = "excluded"


class FileCoverage(BaseModel):
    path: str
    status: CoverageStatus
    reason: str | None = None
