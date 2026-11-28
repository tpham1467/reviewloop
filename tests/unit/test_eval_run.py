import json
import subprocess
from pathlib import Path

import aacr
import pytest
import run as eval_run
from score import Expected

from reviewloop.diff import parse_unified_diff
from reviewloop.models import Finding, PullRequest
from reviewloop.skills.schemas import PROTECTED_CATEGORIES
from reviewloop.stacks import detect_stacks

STACKS = {"typescript", "react", "nestjs", "python", "django", "java", "go", "php", "laravel", "csharp"}


def test_seeded_cases_cover_every_stack_and_protected_category() -> None:
    cases = eval_run.load_seeded()
    assert len(cases) >= 20
    assert {c.stack for c in cases} == STACKS
    assert {e.category for c in cases for e in c.expected} >= PROTECTED_CATEGORIES


@pytest.mark.parametrize("case", eval_run.load_seeded(), ids=lambda c: c.name)
def test_seeded_case_is_consistent(case: eval_run.Case, tmp_path: Path) -> None:
    repo, base, head = case.build(tmp_path)
    assert base != head
    changed = subprocess.run(  # noqa: S603
        ["git", "diff", "--name-only", base, head],  # noqa: S607
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    manifests = {p.name: p.read_text() for p in repo.iterdir() if p.is_file()}
    assert case.stack in detect_stacks(changed, manifests)
    added = {(d.path, ln.new_no) for d in parse_unified_diff((eval_run.SEEDED / case.name / "patch.diff").read_text())
             for h in d.hunks for ln in h.lines if ln.kind == "add"}  # fmt: skip
    for e in case.expected:
        assert e.path in changed
        assert (e.path, e.line) in added, "the expected line must be an added line of the patch"
    assert len((eval_run.SEEDED / case.name / "patch.diff").read_text().splitlines()) < 80


def test_eval_vcs_records_inline_findings_and_reads_files(tmp_path: Path) -> None:
    case = next(c for c in eval_run.load_seeded() if c.name == "ts-null-lookup")
    repo, base, head = case.build(tmp_path)
    pr = PullRequest(
        number=1, title="t", body="", source_branch="feature", target_branch="main", head_sha=head, base_sha=base,
        is_fork=False,
    )  # fmt: skip
    vcs = eval_run.EvalVcs(repo, pr)
    f = Finding(path="x", line=1, severity="low", category="correctness", text="t", existing_code="c")  # type: ignore[arg-type]
    from reviewloop.models import Anchor, Side

    anchor = Anchor(path="src/users.ts", line=9, side=Side.NEW, in_diff=True)
    vcs.post_inline(f.model_copy(update={"id": "f-1"}), anchor)
    assert vcs.anchors == {"f-1": anchor}
    assert vcs.read_file("package.json", "main") is not None
    assert vcs.read_file("missing.txt", "main") is None
    assert vcs.read_file("package.json", "no-such-ref") is None
    vcs.upsert_summary("s")
    assert vcs.summary_body() == "s"
    assert vcs.threads() == [] and vcs.can_write(vcs.bot_identity())
    assert vcs.event().pr_number == 1 and vcs.pull_request() is pr
    vcs.post_general("g")
    vcs.reply(vcs.post_general("g"), "r")
    vcs.resolve(vcs.post_general("g"))
    assert vcs.suggestion_block(Anchor(path="x", line=1, side=Side.NEW, in_diff=True), "c") is None


RAW = [
    {
        "githubPrUrl": "https://github.com/acme/widgets/pull/5",
        "change_line_count": 50,
        "source_commit": "b" * 40,
        "target_commit": "a" * 40,
        "comments": [
            {"category": "Code Defect", "from_line": 10, "path": "a.py", "side": "right"},
            {"category": "Security Vulnerability", "from_line": 20, "path": "b.py", "side": "right"},
            {"category": "Something Else", "from_line": 1, "path": "c.py", "side": "right"},
            {"category": "Performance", "from_line": 3, "path": "d.py", "side": "left"},
        ],
    },
    {
        "githubPrUrl": "https://github.com/acme/big/pull/6",
        "change_line_count": 5000,
        "source_commit": "d" * 40,
        "target_commit": "c" * 40,
        "comments": [{"category": "Code Defect", "from_line": 1, "path": "a.py", "side": "right"}],
    },
    {
        "githubPrUrl": "https://github.com/acme/none/pull/7",
        "change_line_count": 5,
        "source_commit": "f" * 40,
        "target_commit": "e" * 40,
        "comments": [{"category": "Something Else", "from_line": 1, "path": "a.py"}],
    },
]


def test_aacr_parse_maps_categories_and_filters() -> None:
    cases = aacr.parse(RAW, limit=10, max_changed_lines=300)  # type: ignore[arg-type]
    assert [c.name for c in cases] == ["aacr-acme-widgets-5"]
    c = cases[0]
    assert c.repo_url == "https://github.com/acme/widgets.git"
    assert (c.base_commit, c.head_commit) == ("a" * 40, "b" * 40)
    assert c.expected == [
        Expected(path="a.py", line=10, category="correctness"),
        Expected(path="b.py", line=20, category="security"),
    ]
    assert aacr.parse(RAW, limit=1, max_changed_lines=10_000)[0].name == "aacr-acme-widgets-5"  # type: ignore[arg-type]


def test_aacr_load_subset_skips_when_url_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(aacr.URL_ENV, "")
    assert aacr.load_subset() == []


def test_aacr_load_subset_fetches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(aacr.URL_ENV, "https://example.test/d.json")
    monkeypatch.setattr(aacr, "fetch", lambda url: RAW)
    assert len(aacr.load_subset()) == 1


def test_aacr_fetch_rejects_non_https() -> None:
    with pytest.raises(ValueError, match="https"):
        aacr.fetch("http://example.test/d.json")


def test_aacr_fetch_reads_json(monkeypatch: pytest.MonkeyPatch) -> None:
    class Resp:
        def __init__(self, body: bytes) -> None:
            self.body = body

        def __enter__(self) -> "Resp":
            return self

        def __exit__(self, *a: object) -> None:
            return None

        def read(self, n: int) -> bytes:
            return self.body

    monkeypatch.setattr(aacr.urllib.request, "urlopen", lambda url, timeout: Resp(b"[1]"))
    assert aacr.fetch("https://example.test/d.json") == [1]
    monkeypatch.setattr(aacr.urllib.request, "urlopen", lambda url, timeout: Resp(b"{}"))
    with pytest.raises(ValueError, match="list"):
        aacr.fetch("https://example.test/d.json")


def test_build_aacr_checks_out_the_pr_head(tmp_path: Path) -> None:
    origin = tmp_path / "origin"
    origin.mkdir()
    eval_run.git(origin, "init", "-q", "-b", "main")
    (origin / "a.txt").write_text("1\n")
    eval_run.git(origin, "add", ".")
    eval_run.git(origin, "commit", "-q", "-m", "base")
    base = eval_run.git(origin, "rev-parse", "HEAD")
    (origin / "a.txt").write_text("2\n")
    eval_run.git(origin, "commit", "-qam", "head")
    head = eval_run.git(origin, "rev-parse", "HEAD")
    case = aacr.AacrCase("n", origin.as_uri(), base, head, [])
    eval_run.git(origin, "config", "uploadpack.allowAnySHA1InWant", "true")
    root = tmp_path / "work"
    root.mkdir()
    repo, b, h = eval_run.build_aacr(case, root)
    assert (b, h) == (base, head)
    assert (repo / "a.txt").read_text() == "2\n"
    assert eval_run.git(repo, "rev-parse", "main") == base


def _hit(e: Expected) -> Finding:
    return Finding(path=e.path, line=e.line, severity="high", category=e.category, text="t", existing_code="c")  # type: ignore[arg-type]


def _patch_agent(monkeypatch: pytest.MonkeyPatch, found: set[str]) -> None:
    """The fake agent finds the bug of every case named in `found` and raises on the rest."""
    cases = {c.name: c for c in eval_run.load_seeded()}

    def fake_run_case(c: eval_run.Case, env: dict[str, str]) -> list[eval_run.Posted]:
        if c.name in found:
            return [(_hit(e), None) for e in c.expected]
        raise RuntimeError("agent down")

    monkeypatch.setattr(eval_run, "run_case", fake_run_case)
    monkeypatch.setattr(eval_run, "load_seeded", lambda: list(cases.values()))


def test_main_fails_below_floor_without_baseline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    names = [c.name for c in eval_run.load_seeded()]
    out = tmp_path / "m.json"
    _patch_agent(monkeypatch, set(names[:1]))
    assert eval_run.main(["--out", str(out)]) == 1
    data = json.loads(out.read_text())
    assert data["cases"][names[0]]["tp"] == 1
    assert data["seeded"]["fn"] == len(names) - 1
    assert "aacr" not in data
    _patch_agent(monkeypatch, set(names))
    assert eval_run.main(["--out", str(out)]) == 0


def test_main_regression_check_is_separate_from_the_floor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    names = [c.name for c in eval_run.load_seeded()]
    out = tmp_path / "m.json"
    baseline = tmp_path / "baseline.json"
    perfect = {"precision": 1.0, "recall": 1.0, "tp": 0, "fp": 0, "fn": 0}
    baseline.write_text(json.dumps({"seeded": perfect}))
    # Recall 0.7 clears the floor but is 0.3 under the baseline.
    _patch_agent(monkeypatch, set(names[: int(len(names) * 0.7) + 1]))
    assert eval_run.main(["--out", str(out)]) == 0
    assert eval_run.main(["--out", str(out), "--check", str(baseline)]) == 1
    assert eval_run.main(["--out", str(out), "--check", str(tmp_path / "missing.json")]) == 0
    _patch_agent(monkeypatch, set(names))
    assert eval_run.main(["--out", str(out), "--check", str(baseline)]) == 0


def test_main_reports_aacr_apart_from_the_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    names = [c.name for c in eval_run.load_seeded()]
    out = tmp_path / "m.json"
    _patch_agent(monkeypatch, set(names))
    sample = aacr.AacrCase("aacr-x", "u", "a", "b", [Expected(path="a.py", line=1, category="correctness")])
    monkeypatch.setattr(eval_run, "load_subset", lambda n: [sample])
    assert eval_run.main(["--out", str(out), "--aacr", "1"]) == 0
    data = json.loads(out.read_text())
    assert data["aacr"]["fn"] == 1 and data["aacr"]["recall"] == 0.0
    assert data["seeded"]["recall"] == 1.0
    assert "aacr-x" in data["cases"]


def test_posted_findings_counts_inline_general_and_skips_forced_and_old_side() -> None:
    from reviewloop.models import Anchor, Side

    def f(fid: str, **kw: object) -> Finding:
        data = {"id": fid, "path": "agent.py", "line": 5, "severity": "low", "category": "correctness", "text": "t",
                "existing_code": "c", **kw}  # fmt: skip
        return Finding.model_validate(data)

    posted = [f("f-inline"), f("f-general"), f("f-forced", forced=True), f("f-old"), f("f-range")]
    anchors = {
        "f-inline": Anchor(path="a.py", line=9, side=Side.NEW, in_diff=True),
        "f-old": Anchor(path="a.py", line=2, side=Side.OLD, in_diff=True),
        "f-range": Anchor(path="a.py", line=30, side=Side.NEW, in_diff=True, start_line=26),
    }
    got = eval_run.posted_findings(posted, anchors)
    assert [(x.id, x.path, x.line, start) for x, start in got] == [
        ("f-inline", "a.py", 9, None),
        ("f-general", "agent.py", 5, None),
        ("f-range", "a.py", 30, 26),
    ]


def test_run_case_scores_what_the_pipeline_posted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A finding posted as a general thread (no anchor) still counts, at the agent's own line."""
    case = next(c for c in eval_run.load_seeded() if c.name == "ts-null-lookup")
    e = case.expected[0]

    def fake_flow(steps: object, ctx: object) -> None:
        ctx.posted.append(_hit(e).model_copy(update={"id": "f-g"}))  # type: ignore[attr-defined]

    monkeypatch.setattr(eval_run, "run_flow", fake_flow)
    posted = eval_run.run_case(case, {})
    assert eval_run.score(case.expected, [f for f, _ in posted]).tp == 1


def test_main_survives_aacr_load_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    names = [c.name for c in eval_run.load_seeded()]
    _patch_agent(monkeypatch, set(names))

    def boom(n: int) -> list[aacr.AacrCase]:
        raise OSError("offline")

    monkeypatch.setattr(eval_run, "load_subset", boom)
    assert eval_run.main(["--out", str(tmp_path / "m.json"), "--aacr", "3"]) == 0
