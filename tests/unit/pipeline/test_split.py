import re
import threading
import time
from collections import defaultdict
from typing import Any

import pytest

from reviewloop.agents.base import AgentTask
from reviewloop.config import Config
from reviewloop.diff import DiffLine, FileDiff, Hunk, changed_lines
from reviewloop.errors import AgentStepError, ReviewloopError
from reviewloop.models import CoverageStatus, FileCoverage
from reviewloop.output.summary import exit_code
from reviewloop.pipeline.agent import BASE_TIMEOUT_S
from reviewloop.pipeline.engine import run_flow
from reviewloop.pipeline.split import plan_groups
from reviewloop.pipeline.steps.initial import large_diff_review
from reviewloop.pipeline.steps.initial.build_context import BuildContext
from reviewloop.pipeline.steps.initial.initial_review import InitialReview
from reviewloop.pipeline.steps.initial.large_diff_review import LargeDiffReview

_INDEX_ROW = re.compile(r"^\| `([^`]+)` \|", re.MULTILINE)


def fd(path: str, lines: int) -> FileDiff:
    hunk = Hunk(old_start=1, new_start=1, lines=[DiffLine("add", None, i + 1, f"x{i}") for i in range(lines)])
    return FileDiff(path=path, old_path=None, status="modified", is_binary=False, mode_only=False, hunks=[hunk])


def index_paths(task: AgentTask) -> list[str]:
    return _INDEX_ROW.findall((task.workdir / ".reviewloop" / "diff" / "index.md").read_text())


def reviewer(task: AgentTask) -> dict[str, Any]:
    """Reports every file of its sandbox's diff index reviewed, and one finding on the first one."""
    paths = index_paths(task)
    first = {"path": paths[0], "line": 1, "severity": "low", "category": "correctness", "text": f"In {task.step}.",
             "existing_code": "x0"}  # fmt: skip
    return {"findings": [first], "coverage": [{"path": p, "status": "reviewed", "reason": None} for p in paths]}


def split_ctx(make_ctx, diffs: list[FileDiff], max_lines: int = 10, **outputs: Any):
    ctx = make_ctx()
    # Any step without a canned output gets `reviewer`; set after construction since an empty mapping is falsy.
    ctx.agent.outputs = defaultdict(lambda: reviewer, outputs)
    ctx.diffs = diffs
    ctx.config = Config(large_diff_lines=max_lines)
    BuildContext().run(ctx)
    return ctx


def by_path(ctx) -> dict[str, FileCoverage]:
    return {c.path: c for c in ctx.coverage}


def test_groups_respect_max_lines() -> None:
    diffs = [fd("a/1.py", 4), fd("b/1.py", 6), fd("a/2.py", 3), fd("b/2.py", 5), fd("top.py", 2), fd("c/1.py", 1)]

    groups = plan_groups(diffs, max_lines=10)

    assert all(changed_lines(g) <= 10 for g in groups)
    assert sorted(f.path for g in groups for f in g) == sorted(f.path for f in diffs)
    # A directory that fits one group is not split across groups.
    group_of = {f.path: n for n, g in enumerate(groups) for f in g}
    assert group_of["a/1.py"] == group_of["a/2.py"]
    assert group_of["b/1.py"] != group_of["b/2.py"]  # 11 lines: b/ needs two groups


def test_single_huge_file_alone(make_ctx) -> None:
    diffs = [fd("a/small.py", 2), fd("a/huge.py", 25), fd("a/other.py", 3)]

    groups = plan_groups(diffs, max_lines=10)

    assert [[f.path for f in g] for g in groups if any(f.path == "a/huge.py" for f in g)] == [["a/huge.py"]]
    ctx = split_ctx(make_ctx, diffs)
    LargeDiffReview().run(ctx)
    assert by_path(ctx)["a/huge.py"].status is CoverageStatus.REVIEWED


def test_huge_single_file_over_4x_failed(make_ctx) -> None:
    diffs = [fd("a/fine.py", 5), fd("a/giant.py", 41), fd("b/big.py", 40)]

    assert "a/giant.py" not in {f.path for g in plan_groups(diffs, max_lines=10) for f in g}
    ctx = split_ctx(make_ctx, diffs)
    LargeDiffReview().run(ctx)

    coverage = by_path(ctx)
    assert coverage["a/giant.py"] == FileCoverage(path="a/giant.py", status=CoverageStatus.FAILED, reason="too_large")
    assert coverage["b/big.py"].status is CoverageStatus.REVIEWED
    assert all("a/giant.py" not in index_paths(t) for t in ctx.agent.tasks)


def test_overflow_marked_too_large(make_ctx, monkeypatch) -> None:
    diffs = [fd("a/1.py", 8), fd("b/1.py", 8), fd("c/1.py", 8), fd("c/2.py", 1)]
    assert len(plan_groups(diffs, max_lines=10, max_groups=2)) == 2
    monkeypatch.setattr(large_diff_review, "MAX_GROUPS", 2)
    ctx = split_ctx(make_ctx, diffs)

    LargeDiffReview().run(ctx)

    coverage = by_path(ctx)
    assert {p: c.status for p, c in coverage.items() if c.reason != "too_large"} == {
        "a/1.py": CoverageStatus.REVIEWED,
        "b/1.py": CoverageStatus.REVIEWED,
    }
    assert {p for p, c in coverage.items() if c.reason == "too_large"} == {"c/1.py", "c/2.py"}
    assert all(c.status is CoverageStatus.FAILED for c in coverage.values() if c.reason == "too_large")


def test_results_merged_and_coverage_combined(make_ctx) -> None:
    partial = {"findings": [], "coverage": [{"path": "a/1.py", "status": "reviewed", "reason": None}]}
    repaired = {"findings": [], "coverage": [{"path": "a/2.py", "status": "excluded", "reason": "generated"}]}
    diffs = [fd("a/1.py", 4), fd("a/2.py", 4), fd("b/1.py", 9), fd("c/1.py", 7)]
    ctx = split_ctx(make_ctx, diffs, **{"initial-review-g1": [partial, repaired]})
    ctx.excluded = [FileCoverage(path="yarn.lock", status=CoverageStatus.EXCLUDED, reason="lockfile")]
    ctx.coverage = list(ctx.excluded)

    LargeDiffReview().run(ctx)

    assert ctx.split_review
    assert [f.text for f in ctx.findings] == ["In initial-review-g2.", "In initial-review-g3."]
    assert not any(f.security for f in ctx.findings)
    assert [(c.path, c.status, c.reason) for c in ctx.coverage] == [
        ("yarn.lock", CoverageStatus.EXCLUDED, "lockfile"),
        ("a/1.py", CoverageStatus.REVIEWED, None),
        ("a/2.py", CoverageStatus.FAILED, "agent_excluded"),
        ("b/1.py", CoverageStatus.REVIEWED, None),
        ("c/1.py", CoverageStatus.REVIEWED, None),
    ]
    # One coverage-repair call for group 1 only; each call's timeout scales with its own group's lines.
    timeouts = sorted((t.step, t.timeout_s) for t in ctx.agent.tasks)
    assert timeouts == [
        ("initial-review-g1", BASE_TIMEOUT_S + 2 * 8),
        ("initial-review-g1", BASE_TIMEOUT_S + 2 * 8),
        ("initial-review-g2", BASE_TIMEOUT_S + 2 * 9),
        ("initial-review-g3", BASE_TIMEOUT_S + 2 * 7),
    ]


def test_group_failure_marks_its_files_failed_and_others_finish(make_ctx) -> None:
    def broken(task: AgentTask) -> dict[str, Any]:
        raise AgentStepError(f"step '{task.step}': agent timed out\n after 316s")

    diffs = [fd("a/1.py", 8), fd("b/1.py", 8), fd("c/1.py", 8)]
    ctx = split_ctx(make_ctx, diffs, **{"initial-review-g2": broken})

    with pytest.raises(ReviewloopError, match="1 of 3 review groups failed"):
        run_flow([LargeDiffReview()], ctx)

    reason = "group 2: step 'initial-review-g2': agent timed out after 316s"
    # A list, not a dict by path: a file listed twice would show here.
    assert ctx.coverage == [
        FileCoverage(path="a/1.py", status=CoverageStatus.REVIEWED),
        FileCoverage(path="b/1.py", status=CoverageStatus.FAILED, reason=reason),
        FileCoverage(path="c/1.py", status=CoverageStatus.REVIEWED),
    ]
    assert sorted(ctx.agent.steps()) == ["initial-review-g1", "initial-review-g2", "initial-review-g3"]
    summary = ctx.vcs.summary or ""
    assert "Review failed: large-diff review: 1 of 3 review groups failed" in summary
    assert f"- b/1.py: {reason}" in summary
    assert exit_code(ctx.findings, ctx.coverage, "none") == 2


def test_overflow_with_group_failure(make_ctx, monkeypatch) -> None:
    def broken(task: AgentTask) -> dict[str, Any]:
        raise AgentStepError(f"step '{task.step}': agent exited with exit code 1")

    monkeypatch.setattr(large_diff_review, "MAX_GROUPS", 2)
    diffs = [fd("a/1.py", 8), fd("b/1.py", 8), fd("c/1.py", 8), fd("d/1.py", 50)]
    ctx = split_ctx(make_ctx, diffs, **{"initial-review-g1": broken})

    with pytest.raises(ReviewloopError, match="1 of 2 review groups failed"):
        run_flow([LargeDiffReview()], ctx)

    assert ctx.coverage == [
        FileCoverage(path="a/1.py", status=CoverageStatus.FAILED,
                     reason="group 1: step 'initial-review-g1': agent exited with exit code 1"),
        FileCoverage(path="b/1.py", status=CoverageStatus.REVIEWED),
        FileCoverage(path="c/1.py", status=CoverageStatus.FAILED, reason="too_large"),
        FileCoverage(path="d/1.py", status=CoverageStatus.FAILED, reason="too_large"),
    ]  # fmt: skip
    assert sorted(ctx.agent.steps()) == ["initial-review-g1", "initial-review-g2"]


@pytest.mark.parametrize(("lines", "steps"), [(8, ["initial-review-g1", "initial-review-g2"]), (5, ["initial-review"])])
def test_initial_review_skipped_after_split(make_ctx, lines: int, steps: list[str]) -> None:
    ctx = split_ctx(make_ctx, [fd("a/1.py", lines), fd("b/1.py", lines)], **{"initial-review": reviewer})

    # One parallel batch: build-context already decided, so neither step reads a flag the other writes.
    assert LargeDiffReview.group == InitialReview.group == "review"
    run_flow([LargeDiffReview(), InitialReview()], ctx)

    assert ctx.split_review is (lines * 2 > 10)
    assert sorted(ctx.agent.steps()) == steps
    assert [(c.path, c.status) for c in ctx.coverage] == [
        ("a/1.py", CoverageStatus.REVIEWED),
        ("b/1.py", CoverageStatus.REVIEWED),
    ]


def test_each_group_uses_own_sandbox_copy(make_ctx) -> None:
    diffs = [fd("a/1.py", 8), fd("b/1.py", 8), fd("c/1.py", 8)]
    ctx = split_ctx(make_ctx, diffs)

    LargeDiffReview().run(ctx)

    workdirs = {t.step: t.workdir for t in ctx.agent.tasks}
    assert workdirs == {f"initial-review-g{n}": ctx.workdir / f"sandbox-group-{n}" for n in (1, 2, 3)}
    for n, path in enumerate(("a/1.py", "b/1.py", "c/1.py"), start=1):
        inputs = workdirs[f"initial-review-g{n}"] / ".reviewloop"
        assert _INDEX_ROW.findall((inputs / "diff" / "index.md").read_text()) == [path]
        files = inputs / "diff" / "files"
        assert [p.relative_to(files).as_posix() for p in files.rglob("*.diff")] == [f"{path}.diff"]
        assert (inputs / "context.md").read_text() == (ctx.sandbox / ".reviewloop" / "context.md").read_text()
        assert (inputs / "previous-findings.json").is_file()
    # The main sandbox keeps the full diff and gets no group output.
    assert sorted(_INDEX_ROW.findall((ctx.sandbox / ".reviewloop" / "diff" / "index.md").read_text())) == [
        "a/1.py", "b/1.py", "c/1.py"
    ]  # fmt: skip
    assert not list((ctx.sandbox / ".reviewloop" / "out").glob("initial-review-g*"))


def test_parallelism_at_most_5(make_ctx) -> None:
    lock = threading.Lock()
    running = 0
    peak = 0

    def slow(task: AgentTask) -> dict[str, Any]:
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        time.sleep(0.1)
        with lock:
            running -= 1
        return reviewer(task)

    diffs = [fd(f"d{n}/f.py", 8) for n in range(8)]
    ctx = split_ctx(make_ctx, diffs, **{f"initial-review-g{n}": slow for n in range(1, 9)})

    LargeDiffReview().run(ctx)

    assert 1 < peak <= large_diff_review.PARALLEL_GROUPS == 5
    assert len(ctx.agent.tasks) == 8
    assert all(c.status is CoverageStatus.REVIEWED for c in ctx.coverage)


def test_split_runs_in_review_group_with_security_branch(make_ctx, monkeypatch) -> None:
    from reviewloop.pipeline.flows import initial_flow
    from reviewloop.pipeline.steps.initial import security_branch
    from reviewloop.pipeline.steps.initial.pr_overview import PrOverview

    monkeypatch.setattr(security_branch, "run_scanners", lambda *a: [])
    started = threading.Barrier(3, timeout=5)

    def together(answer: dict[str, Any] | None = None):
        def run(task: AgentTask) -> dict[str, Any]:
            # Each of the three steps waits here until the other two have started: they share one batch.
            started.wait()
            return answer or reviewer(task)

        return run

    ctx = split_ctx(make_ctx, [fd("a/1.py", 8), fd("b/1.py", 8)], **{
        "initial-review-g1": together(),
        "initial-review-g2": reviewer,
        "security-review": together({"findings": [], "coverage": []}),
        "pr-overview": together({"markdown": "m"}),
    })  # fmt: skip
    steps = [s for s in initial_flow() if s.group == "review"]
    assert [s.id for s in steps] == ["large-diff-review", "initial-review", "security-branch", "pr-overview"]

    run_flow([LargeDiffReview(), InitialReview(), security_branch.SecurityBranch(), PrOverview()], ctx)

    assert sorted(ctx.agent.steps()) == ["initial-review-g1", "initial-review-g2", "pr-overview", "security-review"]
    assert {t.step: t.workdir.name for t in ctx.agent.tasks} == {
        "initial-review-g1": "sandbox-group-1", "initial-review-g2": "sandbox-group-2",
        "security-review": "sandbox-security", "pr-overview": "sandbox-overview",
    }  # fmt: skip
    assert ctx.overview == "m"
    assert [(c.path, c.status) for c in ctx.coverage] == [
        ("a/1.py", CoverageStatus.REVIEWED),
        ("b/1.py", CoverageStatus.REVIEWED),
    ]
