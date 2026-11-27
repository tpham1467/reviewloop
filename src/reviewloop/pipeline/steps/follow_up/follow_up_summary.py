from reviewloop.models import RunState
from reviewloop.output.summary import Recheck, render_summary
from reviewloop.pipeline.context import RunContext
from reviewloop.pipeline.engine import StopFlow


class FollowUpSummary:
    """Write the re-check counts and the next run-state marker: this head, turn + 1, open minus resolved (R40)."""

    id = "follow-up-summary"
    group: str | None = None

    def run(self, ctx: RunContext) -> None:
        assert ctx.follow_up is not None  # noqa: S101 - set by resolve-target-turn
        follow = ctx.follow_up
        if ctx.overtaken(ctx.current_state()):
            raise StopFlow()
        given = [v.verdict for v in follow.verdicts.values()]
        recheck = Recheck(
            fixed=sum(1 for fid in follow.resolved if follow.verdicts[fid].verdict == "fixed"),
            rebutted=sum(1 for fid in follow.resolved if follow.verdicts[fid].verdict == "valid_rebuttal"),
            still_open=sum(1 for v in given if v in ("not_fixed", "no_response")),
            not_rechecked=[fid for fid in follow.open_ids if fid not in follow.verdicts],
            removed=len(follow.removed),
        )
        resolved = set(follow.resolved)
        open_ids = [fid for fid in follow.open_ids if fid not in resolved]
        ctx.state = RunState(v=1, sha=ctx.pr.head_sha, base=ctx.pr.base_sha, turn=ctx.turn, open=open_ids)
        ctx.vcs.upsert_summary(render_summary("", [], ctx.coverage, ctx.state, "rechecked", recheck=recheck))
