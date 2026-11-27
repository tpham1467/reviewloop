"""Run a flow of steps; consecutive steps of one group run in parallel (spec 5)."""

import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor, wait
from typing import Protocol

from reviewloop.errors import ReviewloopError
from reviewloop.models import RunState
from reviewloop.output.summary import render_notice, render_summary
from reviewloop.pipeline.context import RunContext

# Steps in this group run after the flow ends, whether it succeeded, stopped or failed.
FINALLY = "finally"
MAX_REASON = 200


class Step(Protocol):
    id: str
    group: str | None

    def run(self, ctx: RunContext) -> None: ...


class StopFlow(Exception):
    """Ends a run early without failing it; `notice` replaces the summary text when set."""

    def __init__(self, notice: str | None = None) -> None:
        super().__init__(notice or "flow stopped")
        self.notice = notice


Failure = tuple[Step, BaseException]


def _batches(steps: list[Step]) -> Iterator[list[Step]]:
    batch: list[Step] = []
    for step in steps:
        if batch and (step.group is None or step.group != batch[0].group):
            yield batch
            batch = []
        batch.append(step)
    if batch:
        yield batch


def _run_step(step: Step, ctx: RunContext) -> None:
    start = time.monotonic()
    ctx.log.info("step %s started", step.id)
    step.run(ctx)
    ctx.log.info("step %s done in %.1fs", step.id, time.monotonic() - start)


def _run_batch(batch: list[Step], ctx: RunContext) -> Failure | None:
    """Run one batch; return the first step that raised, after every sibling has finished."""
    if len(batch) == 1:
        try:
            _run_step(batch[0], ctx)
        except BaseException as exc:  # KeyboardInterrupt too: clean still runs, then it is re-raised
            return batch[0], exc
        return None
    with ThreadPoolExecutor(max_workers=len(batch), thread_name_prefix=f"step-{batch[0].group}") as pool:
        futures = [pool.submit(_run_step, step, ctx) for step in batch]
        wait(futures)
    for step, future in zip(batch, futures, strict=True):
        exc = future.exception()
        if exc is not None:
            return step, exc
    return None


def _error_text(exc: BaseException) -> str:
    # Messages of unexpected exceptions may carry internals; ReviewloopError messages are written for users.
    return str(exc) if isinstance(exc, ReviewloopError) else f"internal error ({type(exc).__name__})"


def _write_summary(ctx: RunContext, render: Callable[[RunState], str]) -> None:
    """Write a notice or failure; it keeps the state marker as it is, since this run advanced no turn."""
    current = ctx.current_state()
    if ctx.overtaken(current):
        ctx.log.info("summary left alone: another run advanced the turn")
        return
    ctx.vcs.upsert_summary(render(current or ctx.state or ctx.fresh_state()))


def _report_failure(ctx: RunContext, step: Step, exc: Exception) -> None:
    error = _error_text(exc)
    ctx.fail_uncovered(f"{step.id}: {' '.join(error.split())[:MAX_REASON]}")
    try:
        _write_summary(ctx, lambda state: render_summary(ctx.overview, [], ctx.coverage, state, "failed", error))
    except Exception:
        ctx.log.exception("could not mark the summary failed")


def run_flow(steps: list[Step], ctx: RunContext) -> None:
    """Run `steps` in order; on a failure wait for the group's siblings, mark the summary failed and re-raise."""
    failure: Failure | None = None
    for batch in _batches([s for s in steps if s.group != FINALLY]):
        failure = _run_batch(batch, ctx)
        if failure is not None:
            break
    error: BaseException | None = None
    if failure is not None:
        step, exc = failure
        if isinstance(exc, StopFlow):
            notice = exc.notice
            if notice is not None:
                _write_summary(ctx, lambda state: render_notice(notice, state))
        elif isinstance(exc, Exception):
            _report_failure(ctx, step, exc)
            error = exc
        else:
            # An interrupt or exit: no summary work, only the clean step before it propagates.
            error = exc
    for step in (s for s in steps if s.group == FINALLY):
        try:
            _run_step(step, ctx)
        except Exception:
            if error is None:
                raise
            # The run already failed; that error is the one to report.
            ctx.log.exception("step %s failed after an earlier failure", step.id)
    if error is not None:
        raise error
