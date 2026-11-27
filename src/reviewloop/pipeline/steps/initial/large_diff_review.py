import shutil
from concurrent.futures import ThreadPoolExecutor, wait

from reviewloop.diff import FileDiff, changed_lines
from reviewloop.errors import ReviewloopError
from reviewloop.models import CoverageStatus, FileCoverage, Finding
from reviewloop.pipeline.agent import branch_sandbox, copy_sandbox, write_diff
from reviewloop.pipeline.context import RunContext
from reviewloop.pipeline.failures import failure_reason
from reviewloop.pipeline.review import review_files
from reviewloop.pipeline.split import plan_groups

# Spec 11 starting values: at most 20 groups, at most 5 agent runs at once.
MAX_GROUPS = 20
PARALLEL_GROUPS = 5

GroupResult = tuple[list[Finding], list[FileCoverage]]


def _sandbox_name(n: int) -> str:
    return f"group-{n}"


def _step(n: int) -> str:
    return f"initial-review-g{n}"


class LargeDiffReview:
    """When build-context chose a split, review the diff in groups, one agent run each, and merge (spec 5.1).

    Each group runs in its own sandbox copy whose `.reviewloop/diff/` holds only the group's files. Workers only
    return results; this step's thread alone writes `ctx.findings` and `ctx.coverage`.
    """

    id = "large-diff-review"
    group: str | None = "review"

    def run(self, ctx: RunContext) -> None:
        if not ctx.split_review:
            return
        reviewable = ctx.reviewable()
        groups = plan_groups(reviewable, ctx.config.large_diff_lines, MAX_GROUPS)
        # Copies come from the main sandbox while the review group runs. Nothing else writes it in split mode:
        # initial-review returns, and security-branch and pr-overview use the copies build-context made. All copies
        # exist before any group agent starts, since a run fails when its sandbox changes besides its own output.
        for n, files in enumerate(groups, start=1):
            self._prepare(ctx, n, files)
        pool = ThreadPoolExecutor(max_workers=PARALLEL_GROUPS, thread_name_prefix="review-group")
        try:
            futures = [pool.submit(self._review, ctx, n, files) for n, files in enumerate(groups, start=1)]
            wait(futures)
        except BaseException:
            # An interrupt here: drop groups not started yet; the running ones finish with the pool.
            pool.shutdown(cancel_futures=True)
            raise
        pool.shutdown()

        coverage: dict[str, FileCoverage] = {}
        failed = 0
        for n, (files, future) in enumerate(zip(groups, futures, strict=True), start=1):
            exc = future.exception()
            if exc is not None:
                failed += 1
                reason = failure_reason(f"group {n}", exc)
                coverage.update({fd.path: FileCoverage(path=fd.path, status=CoverageStatus.FAILED, reason=reason)
                                 for fd in files})  # fmt: skip
                continue
            findings, entries = future.result()
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
