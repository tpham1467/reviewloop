from reviewloop.models import RunState
from reviewloop.output.summary import render_notice
from reviewloop.pipeline.context import RunContext


def begin_turn(ctx: RunContext, previous: RunState | None) -> None:
    """Record the turn this run starts from (`previous` is the state marker read now) and the turn it writes."""
    ctx.previous_state = previous
    ctx.start_turn = previous.turn if previous else 0
    ctx.turn = ctx.start_turn + 1
    ctx.state = previous or ctx.fresh_state()


class PreReviewComment:
    """Read the run-state marker (the turn this run starts from) and set the summary to "Reviewing..."."""

    id = "pre-review-comment"
    group: str | None = None

    def run(self, ctx: RunContext) -> None:
        begin_turn(ctx, ctx.current_state())
        assert ctx.state is not None  # noqa: S101 - begin_turn always sets it
        ctx.vcs.upsert_summary(render_notice("Reviewing...", ctx.state))
