from reviewloop.pipeline.context import RunContext
from reviewloop.pipeline.review import review_files

STEP = "initial-review"


class InitialReview:
    """Agent: code-review skill over the whole diff; skipped when large-diff-review reviewed it in groups."""

    id = STEP
    group: str | None = "review"

    def run(self, ctx: RunContext) -> None:
        received = [fd.path for fd in ctx.reviewable()]
        if ctx.split_review or not received:
            return
        ctx.findings, coverage = review_files(ctx, STEP, received, ctx.sandbox)
        ctx.coverage += coverage
