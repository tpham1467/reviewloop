from reviewloop.agents.isolation import prepare_sandbox, strip_checkout_credentials
from reviewloop.pipeline.context import RunContext


class InitContext:
    """Remove git credentials from the CI checkout (R31), then create the work dir and the agent sandbox."""

    id = "init-context"
    group: str | None = None

    def run(self, ctx: RunContext) -> None:
        strip_checkout_credentials(ctx.source_dir)
        ctx.workdir.mkdir(parents=True, exist_ok=True)
        prepare_sandbox(ctx.source_dir, ctx.sandbox)
