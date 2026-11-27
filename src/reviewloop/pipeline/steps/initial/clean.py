import shutil

from reviewloop.pipeline.context import RunContext
from reviewloop.pipeline.engine import FINALLY

KEEP_ENV = "REVIEWLOOP_KEEP_WORKDIR"


class Clean:
    """Remove the work dir after every run, unless REVIEWLOOP_KEEP_WORKDIR=1 (to inspect agent inputs and logs)."""

    id = "clean"
    group: str | None = FINALLY

    def run(self, ctx: RunContext) -> None:
        if ctx.env.get(KEEP_ENV) == "1":
            ctx.log.info("work dir kept at %s", ctx.workdir)
            return
        if ctx.workdir.exists():
            shutil.rmtree(ctx.workdir)
