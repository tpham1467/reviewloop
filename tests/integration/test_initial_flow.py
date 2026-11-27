from pathlib import Path

from reviewloop.agents.base import AgentTask
from reviewloop.models import CoverageStatus, Side
from reviewloop.pipeline.engine import run_flow
from reviewloop.pipeline.flows import follow_up_flow, initial_flow
from reviewloop.state import parse_finding_marker, parse_state_marker

NULL_FINDING = {
    "path": "app.py",
    "line": 3,
    "severity": "high",
    "category": "null-safety",
    "text": "`load` returns `row.name` without checking that `db.get` found a row; return None when row is None.",
    "existing_code": "    return row.name",
}
# The agent's line is 3 off (4 instead of 7); the quote must win.
AUTH_FINDING = {
    "path": "app.py",
    "line": 4,
    "severity": "medium",
    "category": "authorization",
    "text": "`save` writes any user's row; check that the caller owns `user` before `db.write`.",
    "existing_code": "    db.write(user.id, data)",
}


def review_output(task: AgentTask) -> dict[str, object]:
    index = (task.workdir / ".reviewloop" / "diff" / "index.md").read_text()
    assert "app.py" in index and "poetry.lock" in index
    assert (task.workdir / ".reviewloop" / "context.md").is_file()
    return {
        "findings": [NULL_FINDING, AUTH_FINDING],
        "coverage": [{"path": "app.py", "status": "reviewed", "reason": None}],
    }


def test_initial_flow_posts_anchored_findings_and_summary(make_ctx, pr_repo, monkeypatch) -> None:
    from reviewloop.pipeline.steps.initial import security_branch

    # Scanners would run host binaries; this test covers the flow, not them.
    monkeypatch.setattr(security_branch, "run_scanners", lambda *args: [])
    repo, base, head = pr_repo
    ctx = make_ctx(
        source_dir=repo,
        sandbox=False,
        base_sha=base,
        head_sha=head,
        outputs={
            "initial-review": review_output,
            "security-review": {"findings": [], "coverage": []},
            "pr-overview": {"markdown": "Adds `save`."},
            "quality-gate": {"analysis": "Both concrete.", "drop_ids": [], "rewrites": {}},
        },
    )

    run_flow(initial_flow(), ctx)

    vcs = ctx.vcs
    placed = sorted((f.path, a.line, a.side, a.in_diff) for f, a in vcs.inline)
    assert placed == [("app.py", 3, Side.NEW, True), ("app.py", 7, Side.NEW, True)]
    markers = [parse_finding_marker(f.text) for f, _ in vcs.inline]
    assert all(m is not None and m.id.startswith("f-") for m in markers)
    assert vcs.general == []

    assert vcs.summaries[0].startswith("<!-- reviewloop:summary -->\nReviewing...")
    state = parse_state_marker(vcs.summary)
    assert state is not None
    assert state.turn == 1
    assert sorted(state.open) == sorted(m.id for m in markers if m)
    assert "Review complete" in vcs.summary and "Adds `save`." in vcs.summary

    coverage = {c.path: c for c in ctx.coverage}
    assert coverage["app.py"].status is CoverageStatus.REVIEWED
    assert coverage["poetry.lock"].status is CoverageStatus.EXCLUDED
    # The two review branches run in parallel, so only the set of agent steps is fixed.
    assert sorted(ctx.agent.steps()) == ["initial-review", "pr-overview", "quality-gate", "security-review"]
    assert not Path(ctx.workdir).exists()


def test_follow_up_flow_is_empty_until_task_12() -> None:
    assert follow_up_flow() == []
