import json

from reviewloop.models import CoverageStatus, FileCoverage
from reviewloop.pipeline import prompts
from reviewloop.pipeline.agent import run_agent, write_input
from reviewloop.pipeline.context import RunContext
from reviewloop.skills.schemas import ReviewOutput

STEP = "initial-review"
MISSING_FILE = "coverage-missing.json"


def _covered(entry: FileCoverage) -> FileCoverage:
    # Only Python excludes files (ruling R20); a file the agent was sent and skipped was not reviewed.
    if entry.status is CoverageStatus.EXCLUDED:
        return FileCoverage(path=entry.path, status=CoverageStatus.FAILED, reason="agent_excluded")
    return entry


class InitialReview:
    """Agent: code-review skill over the diff; a file counts as reviewed only when the output says so (spec 6.4)."""

    id = STEP
    group: str | None = "review"

    def run(self, ctx: RunContext) -> None:
        received = [fd.path for fd in ctx.reviewable()]
        if not received:
            return
        cfg = ctx.config
        prompt = prompts.review(STEP, cfg.language, cfg.suggestions, cfg.security.enabled)
        out = run_agent(ctx, STEP, ["code-review"], prompt, ReviewOutput)
        reported = {c.path: c for c in out.coverage if c.path in received}
        missing = [p for p in received if p not in reported]
        if missing:
            write_input(ctx.sandbox, MISSING_FILE, json.dumps(missing, indent=2))
            repair = prompts.coverage_repair(prompt, f".reviewloop/{MISSING_FILE}")
            again = run_agent(ctx, STEP, ["code-review"], repair, ReviewOutput)
            reported.update({c.path: c for c in again.coverage if c.path in missing})
        ctx.findings = [f.model_copy(update={"security": False}) for f in out.findings]
        ctx.coverage += [
            _covered(reported[p])
            if p in reported
            else FileCoverage(path=p, status=CoverageStatus.FAILED, reason="not_reported")
            for p in received
        ]
