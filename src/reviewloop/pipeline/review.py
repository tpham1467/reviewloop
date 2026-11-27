"""The code-review agent call over a set of diff files, shared by initial-review and the large-diff groups."""

import json
from pathlib import Path

from reviewloop.models import CoverageStatus, FileCoverage, Finding
from reviewloop.pipeline import prompts
from reviewloop.pipeline.agent import run_agent, write_input
from reviewloop.pipeline.context import RunContext
from reviewloop.skills.schemas import ReviewOutput

MISSING_FILE = "coverage-missing.json"


def _covered(entry: FileCoverage) -> FileCoverage:
    # Only Python excludes files (ruling R20); a file the agent was sent and skipped was not reviewed.
    if entry.status is CoverageStatus.EXCLUDED:
        return FileCoverage(path=entry.path, status=CoverageStatus.FAILED, reason="agent_excluded")
    return entry


def review_files(
    ctx: RunContext, step: str, received: list[str], sandbox: Path, lines: int | None = None
) -> tuple[list[Finding], list[FileCoverage]]:
    """Run the code-review skill over the diff in `sandbox`; a file counts as reviewed only when the output says so
    (spec 6.4). A missing coverage entry gets one repair call. Reads `ctx`, never writes it: groups call this from
    worker threads and the caller merges the results."""
    cfg = ctx.config
    prompt = prompts.review(step, cfg.language, cfg.suggestions, cfg.security.enabled)
    out = run_agent(ctx, step, ["code-review"], prompt, ReviewOutput, sandbox, lines)
    reported = {c.path: c for c in out.coverage if c.path in received}
    missing = [p for p in received if p not in reported]
    if missing:
        write_input(sandbox, MISSING_FILE, json.dumps(missing, indent=2))
        repair = prompts.coverage_repair(prompt, f".reviewloop/{MISSING_FILE}")
        again = run_agent(ctx, step, ["code-review"], repair, ReviewOutput, sandbox, lines)
        reported.update({c.path: c for c in again.coverage if c.path in missing})
    findings = [f.model_copy(update={"security": False}) for f in out.findings]
    coverage = [
        _covered(reported[p])
        if p in reported
        else FileCoverage(path=p, status=CoverageStatus.FAILED, reason="not_reported")
        for p in received
    ]
    return findings, coverage
