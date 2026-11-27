"""Run a flow of steps; consecutive steps of one group run in parallel (spec 5)."""

import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor, wait
from typing import Protocol

from reviewloop.models import RunState
from reviewloop.output.summary import render_notice, render_summary
from reviewloop.pipeline.context import RunContext
from reviewloop.pipeline.failures import error_text, failure_reason

# Steps in this group run after the flow ends, whether it succeeded, stopped or failed.
FINALLY = "finally"


class Step(Protocol):
    id: str
    group: str | None

    def run(self, ctx: RunContext) -> None: ...


class StopFlow(Exception):
    """Ends a run early without failing it; `notice` replaces the summary text when set."""

    def __init__(self, notice: str | None = None) -> None:
        super().__init__(notice or "flow stopped")
        self.notice = notice


class SwitchFlow(Exception):
    """Replaces the steps left in a run with `steps`; the flow's own `finally` steps still run at the end."""

    def __init__(self, steps: list[Step]) -> None:
        super().__init__("flow switched")
        self.steps = steps


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


def _write_summary(ctx: RunContext, render: Callable[[RunState], str]) -> None:
    """Write a notice or failure; it keeps the state marker as it is, since this run advanced no turn."""
    current = ctx.current_state()
    if ctx.overtaken(current):
        ctx.log.info("summary left alone: another run advanced the turn")
        return
    ctx.vcs.upsert_summary(render(current or ctx.state or ctx.fresh_state()))


def _report_failure(ctx: RunContext, step: Step, exc: Exception) -> None:
    error = error_text(exc)
    ctx.fail_uncovered(failure_reason(step.id, exc))
    try:
        _write_summary(ctx, lambda state: render_summary(ctx.overview, [], ctx.coverage, state, "failed", error))
    except Exception:
        ctx.log.exception("could not mark the summary failed")


def _run_main(steps: list[Step], ctx: RunContext) -> Failure | None:
    queue = [s for s in steps if s.group != FINALLY]
    while True:
        failure: Failure | None = None
        for batch in _batches(queue):
            failure = _run_batch(batch, ctx)
            if failure is not None:
                break
        if failure is None or not isinstance(failure[1], SwitchFlow):
            return failure
        queue = [s for s in failure[1].steps if s.group != FINALLY]


def run_flow(steps: list[Step], ctx: RunContext) -> None:
    """Run `steps` in order; on a failure wait for the group's siblings, mark the summary failed and re-raise."""
    failure = _run_main(steps, ctx)
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
