from reviewloop.pipeline import prompts
from reviewloop.pipeline.agent import run_agent
from reviewloop.pipeline.context import RunContext
from reviewloop.skills.schemas import FollowUpOutput, FollowUpVerdict

STEP = "follow-up-review"


class FollowUpReview:
    """Agent: one verdict per open thread; a secret the rescan still finds overrides it with `not_fixed`."""

    id = STEP
    group: str | None = None

    def run(self, ctx: RunContext) -> None:
        assert ctx.follow_up is not None  # noqa: S101 - set by resolve-target-turn
        follow = ctx.follow_up
        if not follow.threads:
            return
        out = run_agent(ctx, STEP, ["follow-up"], prompts.follow_up(STEP, ctx.config.language), FollowUpOutput)
        for verdict in out.verdicts:
            if verdict.thread_id not in follow.threads:
                ctx.log.warning("verdict for thread %r that was not sent; ignored", verdict.thread_id)
                continue
            follow.verdicts.setdefault(verdict.thread_id, verdict)
        for fid, reason in follow.forced.items():
            follow.verdicts[fid] = FollowUpVerdict(thread_id=fid, verdict="not_fixed", reply=reason)
