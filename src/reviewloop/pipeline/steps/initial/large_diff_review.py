from reviewloop.diff import changed_lines
from reviewloop.errors import ReviewloopError
from reviewloop.pipeline.context import RunContext


class LargeDiffReview:
    """Threshold check only; the split review replaces this body (Task 15)."""

    id = "large-diff-review"
    group: str | None = None

    def run(self, ctx: RunContext) -> None:
        if changed_lines(ctx.reviewable()) <= ctx.config.large_diff_lines:
            return
        ctx.fail_uncovered("too_large")
        raise ReviewloopError("diff too large: large-diff split not implemented")
