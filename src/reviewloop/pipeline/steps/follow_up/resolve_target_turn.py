from reviewloop.pipeline.context import FollowUp, RunContext
from reviewloop.pipeline.engine import Step, SwitchFlow
from reviewloop.pipeline.steps.initial.pre_review_comment import begin_turn


class ResolveTargetTurn:
    """Read the run-state marker: previous head, turn and open finding ids (ruling R35).

    It runs before in-progress-comment, unlike the table in spec 5.2: with no valid marker there is nothing to
    re-check, so the run switches to `fallback` (the initial review after the shared steps) before any "Re-checking..."
    comment is posted.
    """

    id = "resolve-target-turn"
    group: str | None = None

    def __init__(self, fallback: list[Step]) -> None:
        self.fallback = fallback

    def run(self, ctx: RunContext) -> None:
        state = ctx.current_state()
        if state is None:
            ctx.log.info("no run-state marker: running a full review")
            raise SwitchFlow(self.fallback)
        begin_turn(ctx, state)
        ctx.follow_up = FollowUp()
