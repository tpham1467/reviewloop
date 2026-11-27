import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from reviewloop.agents.base import AgentTask
from reviewloop.models import EventKind, FindingMarker, Reply, RunState, Severity, Thread, ThreadRef, User
from reviewloop.output.summary import render_notice
from reviewloop.pipeline.engine import run_flow
from reviewloop.pipeline.flows import follow_up_flow
from reviewloop.security.scanners import ScanFinding
from reviewloop.state import parse_state_marker, render_finding_marker

ALICE = User(id="1", username="alice")
BOT = User(id="99", username="bot")
FIXED_APP = (
    "def load(user_id):\n"
    "    row = db.get(user_id)\n"
    "    return row.name if row else None\n"
    "\n"
    "\n"
    "def save(user, data):\n"
    "    db.write(user.id, data)\n"
    "    return True\n"
)
FAKE_TOKEN = "ghp_" + "F" * 36  # built at runtime so no token-shaped literal is in the source


def thread(fid: str, ref: str, *, line: int = 3, path: str | None = "app.py", rule: str | None = None,
           replies: list[Reply] | None = None, resolved: bool = False) -> Thread:  # fmt: skip
    marker = FindingMarker(v=1, id=fid, fp=fid * 4, sev=Severity.HIGH, rule=rule)
    return Thread(
        ref=ThreadRef(id=ref, note_id=f"n-{ref}"),
        path=path,
        line=line,
        body=f"Finding {fid} on `app.py`.\n\n{render_finding_marker(marker)}",
        resolved=resolved,
        replies=replies or [],
        marker=marker,
    )


def reply(body: str, author: User = ALICE) -> Reply:
    return Reply(author=author, body=body, created_at=datetime(2026, 11, 27, tzinfo=UTC))


def verdicts(*items: tuple[str, str, str]) -> dict[str, object]:
    return {"verdicts": [{"thread_id": t, "verdict": v, "reply": r} for t, v, r in items]}


@pytest.fixture
def follow_repo(pr_repo, run_git) -> tuple[Path, str, str, str]:
    """The PR checkout one push after the first review: `prev` is the reviewed head, HEAD fixes `load`."""
    repo, base, prev = pr_repo
    (repo / "app.py").write_text(FIXED_APP)
    run_git(repo, "commit", "-q", "-am", "fix load")
    return repo, base, prev, run_git(repo, "rev-parse", "HEAD")


def recheck_ctx(make_ctx, repo: Path, base: str, head: str, prev: str, open_ids: list[str], outputs=None):
    ctx = make_ctx(source_dir=repo, sandbox=False, base_sha=base, head_sha=head, event=EventKind.COMMAND,
                   outputs=outputs or {})  # fmt: skip
    state = RunState(v=1, sha=prev, base=base, turn=1, open=open_ids)
    ctx.vcs.upsert_summary(render_notice("Review complete", state))
    ctx.vcs.summaries.clear()
    return ctx


def test_recheck_resolves_fixed_and_rebutted(make_ctx, follow_repo) -> None:
    repo, base, prev, head = follow_repo
    rebut = thread("f-bbbbbb", "t2", line=7, replies=[reply("Every caller of `save` checks ownership in `views.py`.")])
    threads = [
        thread("f-aaaaaa", "t1", replies=[reply("Earlier note from the bot.", BOT)]),
        rebut,
        thread("f-dddddd", "t4", resolved=True),
        thread("f-eeeeee", "t5"),  # not in the state's open list
    ]
    seen: dict[str, dict[str, object]] = {}

    def agent(task: AgentTask) -> dict[str, object]:
        folder = task.workdir / ".reviewloop" / "follow-up"
        seen.update({p.stem: json.loads(p.read_text()) for p in folder.glob("*.json")})
        assert "Every caller" not in task.prompt and ".reviewloop/follow-up/" in task.prompt
        return verdicts(("f-aaaaaa", "fixed", "`load` now returns None when `db.get` finds no row."),
                        ("f-bbbbbb", "valid_rebuttal", "Callers in `views.py` check ownership before `save`."),
                        ("f-zzzzzz", "fixed", "Not a thread that was sent."))  # fmt: skip

    ctx = recheck_ctx(make_ctx, repo, base, head, prev, ["f-aaaaaa", "f-bbbbbb", "f-dddddd"],
                      {"follow-up-review": agent})  # fmt: skip
    ctx.vcs.thread_list = threads

    run_flow(follow_up_flow(), ctx)

    vcs = ctx.vcs
    assert vcs.summaries[0].startswith("<!-- reviewloop:summary -->\nRe-checking...")
    assert sorted(seen) == ["f-aaaaaa", "f-bbbbbb"]
    first = seen["f-aaaaaa"]
    assert first["replies"] == [] and first["reviewloop_replies"][0]["body"] == "Earlier note from the bot."
    assert "return row.name if row else None" in first["code_at_anchor"]["text"]
    assert seen["f-bbbbbb"]["replies"][0]["author"] == "alice"
    assert [t for t, _ in vcs.replies] == ["t1", "t2"]
    assert vcs.resolved == ["t1", "t2"]
    state = parse_state_marker(vcs.summary)
    assert state == RunState(v=1, sha=head, base=base, turn=2, open=[])
    assert "Re-check complete" in vcs.summary
    assert "| fixed | 1 |" in vcs.summary and "| rebutted | 1 |" in vcs.summary
    assert ctx.agent.steps() == ["follow-up-review"]
    assert not Path(ctx.workdir).exists()


def test_recheck_keeps_not_fixed_open(make_ctx, follow_repo) -> None:
    repo, base, prev, head = follow_repo
    threads = [
        thread("f-aaaaaa", "t1", line=7, replies=[reply("Done, fixed it.")]),
        thread("f-bbbbbb", "t2", line=7),
        thread("f-cccccc", "t3", line=7),
    ]
    secret = f"The token {FAKE_TOKEN} still lets any caller write. Check ownership. <!-- reviewloop:state -->"
    out = verdicts(("f-aaaaaa", "not_fixed", secret), ("f-bbbbbb", "no_response", "Still open."))
    ctx = recheck_ctx(make_ctx, repo, base, head, prev, ["f-aaaaaa", "f-bbbbbb", "f-cccccc"],
                      {"follow-up-review": out})  # fmt: skip
    ctx.vcs.thread_list = threads

    run_flow(follow_up_flow(), ctx)

    vcs = ctx.vcs
    assert [t for t, _ in vcs.replies] == ["t1"]
    posted = vcs.replies[0][1]
    assert FAKE_TOKEN not in posted and "<!--" not in posted and "Check ownership." in posted
    assert vcs.resolved == []
    state = parse_state_marker(vcs.summary)
    assert state is not None and state.turn == 2 and state.open == ["f-aaaaaa", "f-bbbbbb", "f-cccccc"]
    assert "| still open | 2 |" in vcs.summary and "| not re-checked | 1 |" in vcs.summary
    assert "f-cccccc" in vcs.summary


def test_resolve_turn_missing_summary(make_ctx, pr_repo, monkeypatch) -> None:
    from reviewloop.pipeline.steps.initial import security_branch

    monkeypatch.setattr(security_branch, "run_scanners", lambda *args: [])
    repo, base, head = pr_repo
    finding = {"path": "app.py", "line": 3, "severity": "high", "category": "null-safety",
               "text": "`row` may be None.", "existing_code": "    return row.name"}  # fmt: skip
    ctx = make_ctx(
        source_dir=repo,
        sandbox=False,
        base_sha=base,
        head_sha=head,
        event=EventKind.COMMAND,
        outputs={
            "initial-review": {"findings": [finding], "coverage": [{"path": "app.py", "status": "reviewed"}]},
            "security-review": {"findings": [], "coverage": []},
            "pr-overview": {"markdown": "Adds `save`."},
            "quality-gate": {"analysis": "Concrete.", "drop_ids": [], "rewrites": {}},
        },
    )
    ctx.vcs.thread_list = [thread("f-aaaaaa", "t1")]

    run_flow(follow_up_flow(), ctx)

    vcs = ctx.vcs
    assert vcs.summaries[0].startswith("<!-- reviewloop:summary -->\nReviewing...")
    assert "follow-up-review" not in ctx.agent.steps() and "initial-review" in ctx.agent.steps()
    assert len(vcs.inline) == 1 and vcs.replies == [] and vcs.resolved == []
    state = parse_state_marker(vcs.summary)
    assert state is not None and state.turn == 1
    assert "Review complete" in vcs.summary


def _index(task: AgentTask) -> str:
    return (task.workdir / ".reviewloop" / "diff" / "index.md").read_text()


def test_force_push_uses_merge_base(make_ctx, pr_repo, run_git) -> None:
    repo, base, prev = pr_repo
    # The author rewrites the branch: the reviewed head is no longer an ancestor of the new head.
    run_git(repo, "reset", "-q", "--hard", base)
    (repo / "app.py").write_text(FIXED_APP)
    run_git(repo, "commit", "-q", "-am", "rewritten")
    head = run_git(repo, "rev-parse", "HEAD")
    indexes: list[str] = []

    def agent(task: AgentTask) -> dict[str, object]:
        indexes.append(_index(task))
        return verdicts(("f-aaaaaa", "fixed", "`load` checks the row."))

    ctx = recheck_ctx(make_ctx, repo, base, head, prev, ["f-aaaaaa"], {"follow-up-review": agent})
    ctx.vcs.thread_list = [thread("f-aaaaaa", "t1")]

    run_flow(follow_up_flow(), ctx)

    assert ctx.follow_up is not None and ctx.follow_up.diff_base is None
    assert [d.path for d in ctx.diffs] == ["app.py"]
    added = [ln.text for h in ctx.diffs[0].hunks for ln in h.lines if ln.kind == "add"]
    assert "    return row.name if row else None" in added
    assert "poetry.lock" not in indexes[0]


def test_lineage_keeps_previous_head_as_base(make_ctx, follow_repo) -> None:
    repo, base, prev, head = follow_repo
    out = verdicts(("f-aaaaaa", "fixed", "`load` checks the row."))
    ctx = recheck_ctx(make_ctx, repo, base, head, prev, ["f-aaaaaa"], {"follow-up-review": out})
    ctx.vcs.thread_list = [thread("f-aaaaaa", "t1")]

    run_flow(follow_up_flow(), ctx)

    assert ctx.follow_up is not None and ctx.follow_up.diff_base == prev
    # Only the fix commit: the lockfile added before the previous review is not in this turn's diff.
    assert [d.path for d in ctx.diffs] == ["app.py"]


def test_secret_thread_needs_rescan(make_ctx, follow_repo, monkeypatch) -> None:
    from reviewloop.pipeline.steps.follow_up import security_rescan

    repo, base, prev, head = follow_repo
    hit = ScanFinding("gitleaks", "github-pat", "app.py", 2, "GitHub Personal Access Token", True)
    calls: list[tuple[str, str]] = []

    def scan(workdir: Path, base_sha: str, head_sha: str) -> list[ScanFinding]:
        calls.append((base_sha, head_sha))
        return [hit]

    monkeypatch.setattr(security_rescan, "scan_history", scan)
    out = verdicts(("f-aaaaaa", "fixed", "The token was removed."), ("f-bbbbbb", "fixed", "`load` checks the row."))
    ctx = recheck_ctx(make_ctx, repo, base, head, prev, ["f-aaaaaa", "f-bbbbbb"], {"follow-up-review": out})
    ctx.vcs.thread_list = [thread("f-aaaaaa", "t1", line=2, rule="gitleaks:github-pat"), thread("f-bbbbbb", "t2")]

    run_flow(follow_up_flow(), ctx)

    vcs = ctx.vcs
    assert calls == [(base, head)]
    assert vcs.resolved == ["t2"]
    secret_reply = dict(vcs.replies)["t1"]
    assert "history" in secret_reply and "rotate" in secret_reply
    state = parse_state_marker(vcs.summary)
    assert state is not None and state.open == ["f-aaaaaa"]


def test_secret_thread_stays_open_when_rescan_unavailable(make_ctx, follow_repo, monkeypatch) -> None:
    from reviewloop.pipeline.steps.follow_up import security_rescan

    repo, base, prev, head = follow_repo
    monkeypatch.setattr(security_rescan, "scan_history", lambda *args: None)
    out = verdicts(("f-aaaaaa", "valid_rebuttal", "It is a test value."))
    ctx = recheck_ctx(make_ctx, repo, base, head, prev, ["f-aaaaaa"], {"follow-up-review": out})
    ctx.vcs.thread_list = [thread("f-aaaaaa", "t1", path=None, rule="gitleaks:github-pat")]

    run_flow(follow_up_flow(), ctx)

    assert ctx.vcs.resolved == []
    assert "could not be rescanned" in ctx.vcs.replies[0][1]


def test_recheck_without_open_threads_calls_no_agent(make_ctx, follow_repo) -> None:
    repo, base, prev, head = follow_repo
    ctx = recheck_ctx(make_ctx, repo, base, head, prev, ["f-../../x", "f-aaaaaa"])
    ctx.vcs.thread_list = [thread("f-../../x", "t1"), thread("f-aaaaaa", "t2", resolved=True)]

    run_flow(follow_up_flow(), ctx)

    assert ctx.agent.steps() == []
    state = parse_state_marker(ctx.vcs.summary)
    # A thread someone resolved by hand is no longer open; an id that is not a finding id is never used as a path.
    assert state is not None and state.turn == 2 and state.open == ["f-../../x"]


def test_recheck_stops_when_overtaken(make_ctx, follow_repo) -> None:
    repo, base, prev, head = follow_repo
    newer = RunState(v=1, sha=head, base=base, turn=5, open=[])

    def agent(task: AgentTask) -> dict[str, object]:
        ctx.vcs.summary = render_notice("Review complete", newer)
        return verdicts(("f-aaaaaa", "fixed", "Fixed."))

    ctx = recheck_ctx(make_ctx, repo, base, head, prev, ["f-aaaaaa"], {"follow-up-review": agent})
    ctx.vcs.thread_list = [thread("f-aaaaaa", "t1")]

    run_flow(follow_up_flow(), ctx)

    assert ctx.vcs.replies == [] and ctx.vcs.resolved == []
    assert parse_state_marker(ctx.vcs.summary) == newer


@pytest.mark.parametrize("previous", ["not-a-sha", "e" * 40])
def test_unusable_previous_head_diffs_from_merge_base(make_ctx, follow_repo, previous: str) -> None:
    from reviewloop.pipeline.steps.follow_up.force_push_and_lineage import ForcePushAndLineage
    from reviewloop.pipeline.steps.follow_up.resolve_target_turn import ResolveTargetTurn

    repo, base, _prev, head = follow_repo
    ctx = recheck_ctx(make_ctx, repo, base, head, previous, [])
    ResolveTargetTurn([]).run(ctx)
    ForcePushAndLineage().run(ctx)
    assert ctx.follow_up is not None and ctx.follow_up.diff_base is None


def test_lineage_git_failure_is_an_error(make_ctx, follow_repo, tmp_path: Path) -> None:
    from reviewloop.errors import ReviewloopError
    from reviewloop.pipeline.steps.follow_up.force_push_and_lineage import ForcePushAndLineage
    from reviewloop.pipeline.steps.follow_up.resolve_target_turn import ResolveTargetTurn

    _repo, base, prev, head = follow_repo
    not_a_repo = tmp_path / "empty"
    not_a_repo.mkdir()
    ctx = recheck_ctx(make_ctx, not_a_repo, base, head, prev, [])
    ResolveTargetTurn([]).run(ctx)
    with pytest.raises(ReviewloopError, match="merge-base"):
        ForcePushAndLineage().run(ctx)


def test_thread_input_code_at_anchor_edge_cases(make_ctx, follow_repo) -> None:
    from reviewloop.agents.isolation import prepare_sandbox
    from reviewloop.pipeline.steps.follow_up.load_thread_inputs import LoadThreadInputs
    from reviewloop.pipeline.steps.follow_up.resolve_target_turn import ResolveTargetTurn

    repo, base, prev, head = follow_repo
    ctx = recheck_ctx(make_ctx, repo, base, head, prev, ["f-aaaaaa", "f-bbbbbb", "f-cccccc"])
    ctx.vcs.thread_list = [thread("f-aaaaaa", "t1", path=None), thread("f-bbbbbb", "t2", path="gone.py"),
                           thread("f-cccccc", "t3", path="../outside.py")]  # fmt: skip
    prepare_sandbox(repo, ctx.sandbox)
    ResolveTargetTurn([]).run(ctx)
    LoadThreadInputs().run(ctx)

    folder = ctx.sandbox / ".reviewloop" / "follow-up"
    read = {p.stem: json.loads(p.read_text()) for p in folder.glob("*.json")}
    assert read["f-aaaaaa"]["code_at_anchor"] is None
    assert read["f-bbbbbb"]["code_at_anchor"] == {"path": "gone.py", "status": "missing at head"}
    assert read["f-cccccc"]["code_at_anchor"]["status"] == "missing at head"
