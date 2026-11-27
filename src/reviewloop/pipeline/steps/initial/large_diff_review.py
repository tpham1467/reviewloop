import shutil
from concurrent.futures import Future, ThreadPoolExecutor, wait

from reviewloop.diff import FileDiff, changed_lines
from reviewloop.errors import ReviewloopError
from reviewloop.models import CoverageStatus, FileCoverage, Finding
from reviewloop.pipeline.agent import branch_sandbox, copy_sandbox
from reviewloop.pipeline.context import RunContext
from reviewloop.pipeline.engine import failure_reason
from reviewloop.pipeline.review import review_files
from reviewloop.pipeline.split import plan_groups
from reviewloop.pipeline.steps.initial.build_context import write_diff

# Spec 11 starting values: at most 20 groups, at most 5 agent runs at once.
MAX_GROUPS = 20
PARALLEL_GROUPS = 5

GroupResult = tuple[list[Finding], list[FileCoverage]]


def _sandbox_name(n: int) -> str:
    return f"group-{n}"


def _step(n: int) -> str:
    return f"initial-review-g{n}"


class LargeDiffReview:
    """Above the threshold, review the diff in groups, one agent run per group, and merge the results (spec 5.1).

    Each group runs in its own sandbox copy whose `.reviewloop/diff/` holds only the group's files. Workers only
    return results; this thread alone writes `ctx.findings` and `ctx.coverage`.
    """

    id = "large-diff-review"
    group: str | None = None

    def run(self, ctx: RunContext) -> None:
        reviewable = ctx.reviewable()
        max_lines = ctx.config.large_diff_lines
        if changed_lines(reviewable) <= max_lines:
            return
        ctx.split_review = True
        groups = plan_groups(reviewable, max_lines, MAX_GROUPS)
        # Copies are made before any agent starts: a run fails when its sandbox changes besides its own output.
        for n, files in enumerate(groups, start=1):
            self._prepare(ctx, n, files)
        with ThreadPoolExecutor(max_workers=PARALLEL_GROUPS, thread_name_prefix="review-group") as pool:
            futures = [pool.submit(self._review, ctx, n, files) for n, files in enumerate(groups, start=1)]
            wait(futures)

        coverage: dict[str, FileCoverage] = {}
        failed = 0
        for n, (files, future) in enumerate(zip(groups, futures, strict=True), start=1):
            result = self._result(future)
            if isinstance(result, BaseException):
                failed += 1
                reason = failure_reason(f"group {n}", result)
                coverage.update({fd.path: FileCoverage(path=fd.path, status=CoverageStatus.FAILED, reason=reason)
                                 for fd in files})  # fmt: skip
                continue
            findings, entries = result
            ctx.findings += findings
            coverage.update({c.path: c for c in entries})
        # Files no group took: one too large for any single run, or past the group limit.
        ctx.coverage += [
            coverage.get(fd.path) or FileCoverage(path=fd.path, status=CoverageStatus.FAILED, reason="too_large")
            for fd in reviewable
        ]
        if failed:
            raise ReviewloopError(f"large-diff review: {failed} of {len(groups)} review groups failed")

    @staticmethod
    def _prepare(ctx: RunContext, n: int, files: list[FileDiff]) -> None:
        copy_sandbox(ctx, _sandbox_name(n))
        sandbox = branch_sandbox(ctx, _sandbox_name(n))
        shutil.rmtree(sandbox / ".reviewloop" / "diff")
        write_diff(sandbox, files, {})

    @staticmethod
    def _review(ctx: RunContext, n: int, files: list[FileDiff]) -> GroupResult:
        sandbox = branch_sandbox(ctx, _sandbox_name(n))
        return review_files(ctx, _step(n), [fd.path for fd in files], sandbox, changed_lines(files))

    @staticmethod
    def _result(future: "Future[GroupResult]") -> GroupResult | Exception:
        exc = future.exception()
        if exc is None:
            return future.result()
        if not isinstance(exc, Exception):
            # An interrupt or exit is not a group failure; let it stop the run.
            raise exc
        return exc
