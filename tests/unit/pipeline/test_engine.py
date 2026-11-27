import threading
import time

import pytest

from reviewloop.errors import ReviewloopError
from reviewloop.models import RunState
from reviewloop.pipeline.engine import FINALLY, StopFlow, run_flow
from reviewloop.state import parse_state_marker, render_state_marker

PREVIOUS = RunState(v=1, sha="c" * 40, base="a" * 40, turn=3, open=["f-abc123"])


class Recorder:
    def __init__(self, step_id: str, group: str | None = None, *, fail: BaseException | None = None, delay=0.0):
        self.id = step_id
        self.group = group
        self.fail = fail
        self.delay = delay
        self.done = threading.Event()
        self.thread: str | None = None

    def run(self, ctx) -> None:
        self.thread = threading.current_thread().name
        time.sleep(self.delay)
        if self.fail is not None:
            raise self.fail
        self.done.set()


def test_parallel_group_waits_for_siblings(make_ctx) -> None:
    ctx = make_ctx()
    ctx.state = PREVIOUS
    failing = Recorder("a", "review", fail=ReviewloopError("agent crashed"))
    slow = Recorder("b", "review", delay=0.3)
    after = Recorder("c")
    clean = Recorder("clean", FINALLY)

    with pytest.raises(ReviewloopError, match="agent crashed"):
        run_flow([failing, slow, after, clean], ctx)

    assert slow.done.is_set()
    assert not after.done.is_set()
    assert "Review failed: agent crashed" in ctx.vcs.summaries[0]
    assert clean.done.is_set()


def test_group_steps_run_on_separate_threads(make_ctx) -> None:
    ctx = make_ctx()
    a, b = Recorder("a", "g", delay=0.05), Recorder("b", "g", delay=0.05)

    run_flow([a, b], ctx)

    assert a.thread != b.thread
    assert ctx.vcs.summaries == []


def test_failure_updates_summary_failed(make_ctx) -> None:
    ctx = make_ctx()
    ctx.state = PREVIOUS
    clean = Recorder("clean", FINALLY)

    with pytest.raises(ReviewloopError):
        run_flow([Recorder("boom", fail=ReviewloopError("diff failed")), Recorder("never"), clean], ctx)

    body = ctx.vcs.summary
    assert body is not None
    assert "Review failed: diff failed" in body
    assert parse_state_marker(body) == PREVIOUS
    assert clean.done.is_set()


def test_failure_summary_error_keeps_original_error(make_ctx) -> None:
    ctx = make_ctx()
    ctx.state = PREVIOUS

    def broken(body: str) -> None:
        raise ReviewloopError("vcs down")

    ctx.vcs.upsert_summary = broken
    with pytest.raises(ReviewloopError, match="step failed"):
        run_flow([Recorder("boom", fail=ReviewloopError("step failed"))], ctx)


def test_stop_flow_posts_notice_and_runs_finally(make_ctx) -> None:
    ctx = make_ctx()
    ctx.state = PREVIOUS
    clean = Recorder("clean", FINALLY)

    run_flow([Recorder("gate", fail=StopFlow("Review skipped: no ticket.")), Recorder("never"), clean], ctx)

    assert (
        ctx.vcs.summary == f"<!-- reviewloop:summary -->\nReview skipped: no ticket.\n\n{render_state_marker(PREVIOUS)}"
    )
    assert clean.done.is_set()


def test_stop_flow_without_notice_posts_nothing(make_ctx) -> None:
    ctx = make_ctx()
    run_flow([Recorder("gate", fail=StopFlow())], ctx)
    assert ctx.vcs.summaries == []


def test_failure_before_state_is_read_uses_fresh_state(make_ctx) -> None:
    ctx = make_ctx()
    with pytest.raises(ReviewloopError):
        run_flow([Recorder("boom", fail=ReviewloopError("bad config"))], ctx)
    state = parse_state_marker(ctx.vcs.summary)
    assert state is not None and state.turn == 0 and state.sha == ctx.pr.head_sha


def test_unexpected_error_hides_its_message(make_ctx) -> None:
    ctx = make_ctx()
    with pytest.raises(KeyError):
        run_flow([Recorder("boom", fail=KeyError("internal detail"))], ctx)
    assert "internal error (KeyError)" in ctx.vcs.summary and "internal detail" not in ctx.vcs.summary


def test_failed_step_marks_uncovered_files_failed(make_ctx) -> None:
    from reviewloop.diff import parse_unified_diff
    from reviewloop.errors import AgentStepError
    from reviewloop.models import CoverageStatus, FileCoverage
    from reviewloop.pipeline.steps.initial.initial_review import InitialReview

    def crash(task):
        raise AgentStepError("step 'initial-review': agent timed out after 300s")

    ctx = make_ctx(outputs={"initial-review": crash})
    ctx.state = PREVIOUS
    ctx.diffs = parse_unified_diff(
        "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x\n+y\n--- a/b.lock\n+++ b/b.lock\n@@ -1 +1 @@\n-x\n+y\n"
    )
    ctx.excluded = [FileCoverage(path="b.lock", status=CoverageStatus.EXCLUDED, reason="lockfile")]
    ctx.coverage = list(ctx.excluded)

    with pytest.raises(AgentStepError):
        run_flow([InitialReview()], ctx)

    assert ctx.coverage[-1] == FileCoverage(
        path="a.py",
        status=CoverageStatus.FAILED,
        reason="initial-review: step 'initial-review': agent timed out after 300s",
    )
    assert "Failed files:" in ctx.vcs.summary and "- a.py: initial-review:" in ctx.vcs.summary


def test_clean_failure_keeps_original_error(make_ctx) -> None:
    ctx = make_ctx()
    clean = Recorder("clean", FINALLY, fail=OSError("rmtree failed"))
    with pytest.raises(ReviewloopError, match="the real error"):
        run_flow([Recorder("boom", fail=ReviewloopError("the real error")), clean], ctx)


def test_clean_failure_after_success_is_raised(make_ctx) -> None:
    ctx = make_ctx()
    with pytest.raises(OSError, match="rmtree failed"):
        run_flow([Recorder("ok"), Recorder("clean", FINALLY, fail=OSError("rmtree failed"))], ctx)


def test_stop_flow_in_parallel_group_stops(make_ctx) -> None:
    ctx = make_ctx()
    ctx.state = PREVIOUS
    after = Recorder("after")
    run_flow([Recorder("a", "g", fail=StopFlow("Stopped.")), Recorder("b", "g"), after], ctx)
    assert "Stopped." in ctx.vcs.summary and not after.done.is_set()


def test_base_exception_in_parallel_group_waits_and_cleans(make_ctx) -> None:
    ctx = make_ctx()
    ctx.state = PREVIOUS
    slow = Recorder("slow", "g", delay=0.2)
    clean = Recorder("clean", FINALLY)
    with pytest.raises(KeyboardInterrupt):
        run_flow([Recorder("a", "g", fail=KeyboardInterrupt()), slow, clean], ctx)
    assert slow.done.is_set() and clean.done.is_set()


def test_base_exception_keeps_original_when_clean_fails(make_ctx) -> None:
    ctx = make_ctx()
    clean = Recorder("clean", FINALLY, fail=OSError("rmtree failed"))
    with pytest.raises(SystemExit):
        run_flow([Recorder("a", fail=SystemExit(3)), clean], ctx)
