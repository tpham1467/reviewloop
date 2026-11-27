from reviewloop.output.summary import render_notice
from reviewloop.pipeline.context import RunContext


class PreReviewComment:
    """Read the run-state marker (the turn this run starts from) and set the summary to "Reviewing..."."""

    id = "pre-review-comment"
    group: str | None = None

    def run(self, ctx: RunContext) -> None:
        ctx.previous_state = ctx.current_state()
        ctx.start_turn = ctx.previous_state.turn if ctx.previous_state else 0
        ctx.turn = ctx.start_turn + 1
        ctx.state = ctx.previous_state or ctx.fresh_state()
        ctx.vcs.upsert_summary(render_notice("Reviewing...", ctx.state))
