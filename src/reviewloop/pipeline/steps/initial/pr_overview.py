from reviewloop.pipeline import prompts
from reviewloop.pipeline.agent import OVERVIEW_SANDBOX, branch_sandbox, run_agent
from reviewloop.pipeline.context import RunContext
from reviewloop.skills.schemas import OverviewOutput

STEP = "pr-overview"


class PrOverview:
    """Agent: overview text for the summary comment."""

    id = STEP
    group: str | None = "review"

    def run(self, ctx: RunContext) -> None:
        if not ctx.reviewable():
            return
        sandbox = branch_sandbox(ctx, OVERVIEW_SANDBOX)
        out = run_agent(ctx, STEP, ["pr-overview"], prompts.pr_overview(STEP, ctx.config.language), OverviewOutput,
                        sandbox)  # fmt: skip
        ctx.overview = out.markdown
