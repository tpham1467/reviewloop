from reviewloop.output.summary import render_notice
from reviewloop.pipeline.context import RunContext


class InProgressComment:
    """Set the summary to "Re-checking...", keeping the state marker resolve-target-turn read."""

    id = "in-progress-comment"
    group: str | None = None

    def run(self, ctx: RunContext) -> None:
        assert ctx.state is not None  # noqa: S101 - resolve-target-turn sets it
        ctx.vcs.upsert_summary(render_notice("Re-checking...", ctx.state))
