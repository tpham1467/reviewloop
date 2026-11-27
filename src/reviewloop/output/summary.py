import math
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
_STATUS = {"reviewing": "Reviewing...", "rechecking": "Re-checking...", "done": "Review complete"}


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
    status: Literal["reviewing", "rechecking", "done", "failed"],
    error: str | None = None,
) -> str:
    status_line = (
        f"Review failed: {_clean(error or 'unknown error', one_line=True)}" if status == "failed" else _STATUS[status]
    )
    lines = [SUMMARY_HEADER, status_line, "", _clean(overview), ""]
    lines += ["| Severity | Count |", "| --- | --- |"]
    for sev in Severity:
        lines.append(f"| {sev.value} | {sum(1 for f in findings if f.severity is sev)} |")
    reviewed = sum(1 for c in coverage if c.status is CoverageStatus.REVIEWED)
    lines += ["", f"Files: Reviewed {reviewed} of {len(coverage)} files"]
    failed = [c for c in coverage if c.status is CoverageStatus.FAILED]
    if failed:
        lines += ["", "Failed files:"]
        lines += [f"- {c.path}: {_clean(c.reason or 'unknown', one_line=True)}" for c in failed]
    lines += ["", f"Score: {score(findings, coverage)}/10", render_state_marker(state)]
    return "\n".join(lines)


def render_notice(text: str, state: RunState) -> str:
    """A one-line summary ("Reviewing...", "Review skipped: ...") that keeps the run-state marker."""
    return f"{SUMMARY_HEADER}\n{_clean(text, one_line=True)}\n\n{render_state_marker(state)}"
