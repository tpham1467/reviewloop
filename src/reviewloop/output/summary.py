import math
from dataclasses import dataclass, field
from typing import Literal

from reviewloop.models import CoverageStatus, FileCoverage, Finding, RunState, Severity
from reviewloop.output.redact import safe_text
from reviewloop.state import render_state_marker

_PENALTY = {
    Severity.CRITICAL: 4.0,
    Severity.HIGH: 2.0,
    Severity.MEDIUM: 1.0,
    Severity.LOW: 0.25,
}
SUMMARY_HEADER = "<!-- reviewloop:summary -->"
_STATUS = {
    "reviewing": "Reviewing...",
    "rechecking": "Re-checking...",
    "done": "Review complete",
    "rechecked": "Re-check complete",
}


@dataclass
class Recheck:
    """Outcome of a `/reviewloop recheck` turn, by thread."""

    fixed: int = 0
    rebutted: int = 0
    still_open: int = 0
    # Open finding ids whose thread got no verdict this turn.
    not_rechecked: list[str] = field(default_factory=list[str])
    # Open finding ids whose thread no longer exists on the platform; dropped from the open list.
    removed: int = 0


def _any_failed(coverage: list[FileCoverage]) -> bool:
    return any(c.status is CoverageStatus.FAILED for c in coverage)


def score(findings: list[Finding], coverage: list[FileCoverage]) -> int:
    value = max(0, math.floor(10 - sum(_PENALTY[f.severity] for f in findings)))
    return min(value, 9) if _any_failed(coverage) else value


def exit_code(findings: list[Finding], coverage: list[FileCoverage], fail_on: str) -> int:
    if _any_failed(coverage):
        return 2
    blocking = {"critical": {Severity.CRITICAL}, "high": {Severity.CRITICAL, Severity.HIGH}}
    if any(f.severity in blocking.get(fail_on, set()) for f in findings):
        return 1
    return 0


def _clean(text: str, one_line: bool = False) -> str:
    text = safe_text(text)
    return " ".join(text.split()) if one_line else text


def render_summary(
    overview: str,
    findings: list[Finding],
    coverage: list[FileCoverage],
    state: RunState,
    status: Literal["reviewing", "rechecking", "done", "rechecked", "failed"],
    error: str | None = None,
    recheck: Recheck | None = None,
) -> str:
    """The summary comment. With `recheck` it shows the thread outcomes of a re-check turn in place of the overview,
    the severity table and the score, which describe an initial review's findings."""
    status_line = (
        f"Review failed: {_clean(error or 'unknown error', one_line=True)}" if status == "failed" else _STATUS[status]
    )
    lines = [SUMMARY_HEADER, status_line, ""]
    if recheck is None:
        lines += [_clean(overview), "", "| Severity | Count |", "| --- | --- |"]
        for sev in Severity:
            lines.append(f"| {sev.value} | {sum(1 for f in findings if f.severity is sev)} |")
    else:
        lines += ["| Re-check | Threads |", "| --- | --- |", f"| fixed | {recheck.fixed} |",
                  f"| rebutted | {recheck.rebutted} |", f"| still open | {recheck.still_open} |",
                  f"| not re-checked | {len(recheck.not_rechecked)} |", f"| removed | {recheck.removed} |"]  # fmt: skip
        if recheck.not_rechecked:
            ids = ", ".join(f"`{_clean(i, one_line=True)}`" for i in recheck.not_rechecked)
            lines += ["", f"Not re-checked: {ids}"]
    reviewed = sum(1 for c in coverage if c.status is CoverageStatus.REVIEWED)
    if recheck is None:
        lines += ["", f"Files: Reviewed {reviewed} of {len(coverage)} files"]
    failed = [c for c in coverage if c.status is CoverageStatus.FAILED]
    if failed:
        lines += ["", "Failed files:"]
        lines += [f"- {c.path}: {_clean(c.reason or 'unknown', one_line=True)}" for c in failed]
    if recheck is None:
        lines += ["", f"Score: {score(findings, coverage)}/10"]
    else:
        lines.append("")
    lines.append(render_state_marker(state))
    return "\n".join(lines)


def render_notice(text: str, state: RunState) -> str:
    """A one-line summary ("Reviewing...", "Review skipped: ...") that keeps the run-state marker."""
    return f"{SUMMARY_HEADER}\n{_clean(text, one_line=True)}\n\n{render_state_marker(state)}"
