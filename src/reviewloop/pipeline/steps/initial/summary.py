from reviewloop.models import RunState
from reviewloop.output.summary import render_summary
from reviewloop.pipeline.context import RunContext
from reviewloop.pipeline.engine import StopFlow


class Summary:
    """Write the final summary and the run-state marker for this turn."""

    id = "summary"
    group: str | None = None

    def run(self, ctx: RunContext) -> None:
        if ctx.overtaken(ctx.current_state()):
            raise StopFlow()
        open_ids = [t.marker.id for t in ctx.previous_threads if t.marker is not None and not t.resolved]
        open_ids += [f.id for f in ctx.posted if f.id is not None]
        ctx.state = RunState(
            v=1, sha=ctx.pr.head_sha, base=ctx.pr.base_sha, turn=ctx.turn, open=list(dict.fromkeys(open_ids))
        )
        ctx.vcs.upsert_summary(render_summary(ctx.overview, ctx.findings, ctx.coverage, ctx.state, "done"))
