from reviewloop.agents.registry import get_adapter
from reviewloop.config import load_config
from reviewloop.pipeline.context import RunContext
from reviewloop.trackers.registry import get_tracker

CONFIG_FILE = ".reviewloop.yml"


class LoadConfig:
    """Read `.reviewloop.yml` from the target branch only (spec 6.3); build the agent and tracker it names.

    The agent's credentials are checked here, before pre-review-comment posts "Reviewing...", so a run that cannot
    call its agent fails without first announcing a review."""

    id = "load-config"
    group: str | None = None

    def run(self, ctx: RunContext) -> None:
        ctx.config = load_config(ctx.vcs.read_file(CONFIG_FILE, ctx.pr.target_branch))
        if ctx.agent is None:
            ctx.agent = get_adapter(ctx.config.agent)
        ctx.agent.check_auth()
        tracker = ctx.config.tracker
        if ctx.tracker is None and tracker is not None:
            ctx.tracker = get_tracker(tracker.type, tracker.url, ctx.env)
