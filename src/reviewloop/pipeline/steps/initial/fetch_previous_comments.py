from reviewloop.pipeline.context import RunContext


class FetchPreviousComments:
    """Load the bot's finding threads; their markers drive dedup and the open list (spec 6.2)."""

    id = "fetch-previous-comments"
    group: str | None = None

    def run(self, ctx: RunContext) -> None:
        ctx.previous_threads = [t for t in ctx.vcs.threads() if t.marker is not None]
