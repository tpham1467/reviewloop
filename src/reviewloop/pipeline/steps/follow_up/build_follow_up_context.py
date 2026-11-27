from reviewloop.pipeline.agent import write_diff
from reviewloop.pipeline.context import RunContext


class BuildFollowUpContext:
    """Write the diff since the previous review to `.reviewloop/diff/`; the thread files are already there."""

    id = "build-follow-up-context"
    group: str | None = None

    def run(self, ctx: RunContext) -> None:
        write_diff(ctx.sandbox, ctx.diffs, {c.path: c.reason for c in ctx.excluded})
