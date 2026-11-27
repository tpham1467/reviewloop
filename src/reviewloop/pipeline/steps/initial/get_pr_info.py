from reviewloop.models import EventKind
from reviewloop.pipeline.context import RunContext
from reviewloop.pipeline.engine import StopFlow


class GetPrInfo:
    """Log the PR being reviewed; an opened draft stops the run unless `review_drafts` is set."""

    id = "get-pr-info"
    group: str | None = None

    def run(self, ctx: RunContext) -> None:
        pr = ctx.pr
        ctx.log.info("PR #%s %s -> %s head %s fork=%s", pr.number, pr.source_branch, pr.target_branch, pr.head_sha,
                     pr.is_fork)  # fmt: skip
        if ctx.event.kind is EventKind.OPENED and pr.draft and not ctx.config.review_drafts:
            # A command on a draft is an explicit request, so only the automatic trigger is skipped.
            raise StopFlow()
