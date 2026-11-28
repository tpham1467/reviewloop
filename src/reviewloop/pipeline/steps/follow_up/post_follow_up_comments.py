from reviewloop.output.redact import safe_text
from reviewloop.pipeline.context import RunContext
from reviewloop.pipeline.engine import StopFlow

RESOLVING = frozenset({"fixed", "valid_rebuttal"})


class PostFollowUpComments:
    """Reply in each re-checked thread and resolve the fixed and validly rebutted ones (ruling R39)."""

    id = "post-follow-up-comments"
    group: str | None = None

    def run(self, ctx: RunContext) -> None:
        ctx.posting_started = True
        assert ctx.follow_up is not None  # noqa: S101 - set by resolve-target-turn
        follow = ctx.follow_up
        # Checked right before posting: the agent call above can take minutes.
        if ctx.overtaken(ctx.current_state()):
            ctx.log.info("another run advanced the turn; not posting")
            raise StopFlow()
        for fid, thread in follow.threads.items():
            verdict = follow.verdicts.get(fid)
            if verdict is None or verdict.verdict == "no_response":
                continue
            ctx.vcs.reply(thread.ref, safe_text(verdict.reply))
            if verdict.verdict in RESOLVING:
                ctx.vcs.resolve(thread.ref)
                follow.resolved.append(fid)
