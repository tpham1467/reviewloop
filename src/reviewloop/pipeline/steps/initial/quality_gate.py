from reviewloop.models import Finding, Severity
from reviewloop.pipeline import prompts
from reviewloop.pipeline.agent import numbered, run_agent, write_input
from reviewloop.pipeline.context import RunContext
from reviewloop.pipeline.findings import is_forced
from reviewloop.skills.schemas import PROTECTED_CATEGORIES, GateOutput

STEP = "quality-gate"


def protected(f: Finding) -> bool:
    return f.category in PROTECTED_CATEGORIES or f.severity is Severity.CRITICAL


class QualityGate:
    """Agent proposes drops and rewrites; Python applies them and never drops a protected finding (spec 5.3)."""

    id = STEP
    group: str | None = None

    def run(self, ctx: RunContext) -> None:
        candidates = [f for f in ctx.findings if not is_forced(f)]
        forced = [f for f in ctx.findings if is_forced(f)]
        if not candidates:
            return
        write_input(ctx.sandbox, "gate/findings.json", numbered(candidates))
        out = run_agent(ctx, STEP, ["quality-gate"], prompts.quality_gate(STEP, ctx.config.language), GateOutput)
        drop = set(out.drop_ids)
        kept: list[Finding] = list(forced)
        for i, f in enumerate(candidates):
            if i in drop and not protected(f):
                continue
            rewrite = out.rewrites.get(i)
            kept.append(f.model_copy(update={"text": rewrite}) if rewrite else f)
        ctx.findings = kept
