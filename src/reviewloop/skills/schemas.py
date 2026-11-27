"""Pydantic output schemas for the agent steps; the skills describe the same shapes in prose."""

from typing import Literal

from pydantic import BaseModel, field_validator

from reviewloop.models import CoverageStatus, FileCoverage, Finding

CATEGORIES = frozenset(
    {
        "correctness",
        "api-contract",
        "concurrency",
        "authorization",
        "data-loss",
        "null-safety",
        "security",
        "error-handling",
        "resource-leak",
        "performance",
        "maintainability",
    }
)
PROTECTED_CATEGORIES = frozenset({"api-contract", "concurrency", "authorization", "data-loss", "null-safety"})


def _check_findings(findings: list[Finding]) -> None:
    for n, finding in enumerate(findings):
        if finding.category not in CATEGORIES:
            allowed = ", ".join(sorted(CATEGORIES))
            raise ValueError(f"unknown category {finding.category!r}; use one of: {allowed}")
        if finding.forced:
            raise ValueError(f"finding {n} sets a field reviewloop reserves; remove `forced`")


class ReviewOutput(BaseModel):
    findings: list[Finding]
    coverage: list[FileCoverage]

    @field_validator("findings")
    @classmethod
    def _valid_findings(cls, findings: list[Finding]) -> list[Finding]:
        _check_findings(findings)
        for n, finding in enumerate(findings):
            if not finding.existing_code.strip():
                raise ValueError(f"finding {n} has a blank existing_code; quote the code verbatim")
        return findings

    @field_validator("coverage")
    @classmethod
    def _reason_when_not_reviewed(cls, coverage: list[FileCoverage]) -> list[FileCoverage]:
        for entry in coverage:
            if entry.status != CoverageStatus.REVIEWED and not entry.reason:
                raise ValueError(f"coverage for {entry.path!r} has status {entry.status.value} and needs a reason")
        return coverage


class AnchorRepairOutput(BaseModel):
    """The review shape for anchor repair; a blank quote is allowed and means the agent did not find the code."""

    findings: list[Finding]
    coverage: list[FileCoverage]

    @field_validator("findings")
    @classmethod
    def _valid_findings(cls, findings: list[Finding]) -> list[Finding]:
        _check_findings(findings)
        return findings


class GateOutput(BaseModel):
    analysis: str
    drop_ids: list[int]
    rewrites: dict[int, str]


class FollowUpVerdict(BaseModel):
    thread_id: str
    verdict: Literal["fixed", "not_fixed", "valid_rebuttal", "no_response"]
    reply: str


class FollowUpOutput(BaseModel):
    verdicts: list[FollowUpVerdict]


class OverviewOutput(BaseModel):
    markdown: str


class ContextOutput(BaseModel):
    summary: str
    has_context: bool


class SecurityVerifyOutput(BaseModel):
    kept_ids: list[int]
    analysis: str
