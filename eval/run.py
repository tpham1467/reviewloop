"""Run the eval: build each case as a throwaway git repo, run the real initial flow, score what was posted.

Usage: python eval/run.py [--aacr N] [--only NAME] [--out metrics.json] [--check baseline.json]
To set the baseline, download the `eval-metrics` artifact of a green run and commit it as eval/baseline.json.
Needs an agent credential in the environment (for example ANTHROPIC_API_KEY).
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from aacr import AacrCase, load_subset
from score import Expected, Metrics, aggregate, below_floor, regressions, score

from reviewloop.models import Anchor, Event, EventKind, Finding, PullRequest, Side, Thread, ThreadRef, User
from reviewloop.pipeline.context import RunContext
from reviewloop.pipeline.engine import run_flow
from reviewloop.pipeline.flows import initial_flow

SEEDED = Path(__file__).parent / "seeded"
GIT = ("git", "-c", "user.email=eval@example.test", "-c", "user.name=Eval")


# A finding the pipeline posted, with the first line of its multi-line anchor (None for a single line).
Posted = tuple[Finding, int | None]
Build = Callable[[Path], tuple[Path, str, str]]


@dataclass(frozen=True)
class Case:
    name: str
    stack: str
    expected: list[Expected]
    # Makes the PR checkout under a directory: (repo with HEAD at the PR head, base sha, head sha).
    build: Build


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run([*GIT, *args], cwd=cwd, check=True, capture_output=True, text=True)  # noqa: S603
    return result.stdout.strip()


class EvalVcs:
    """In-memory VcsAdapter that records posted findings; files are read from the case's git repo."""

    def __init__(self, repo: Path, pr: PullRequest) -> None:
        self.repo = repo
        self.pr = pr
        self.summary: str | None = None
        self.anchors: dict[str, Anchor] = {}
        self.count = 0

    def event(self) -> Event:
        return Event(kind=EventKind.OPENED, pr_number=self.pr.number)

    def pull_request(self) -> PullRequest:
        return self.pr

    def bot_identity(self) -> User:
        return User(id="0", username="reviewloop-eval")

    def can_write(self, user: User) -> bool:
        return True

    def threads(self) -> list[Thread]:
        return []

    def post_inline(self, finding: Finding, anchor: Anchor) -> ThreadRef:
        self.count += 1
        if finding.id is not None:
            self.anchors[finding.id] = anchor
        return ThreadRef(id=f"i{self.count}")

    def post_general(self, body: str) -> ThreadRef:
        return ThreadRef(id="g")

    def reply(self, thread: ThreadRef, body: str) -> None:
        return None

    def resolve(self, thread: ThreadRef) -> None:
        return None

    def upsert_summary(self, body: str) -> None:
        self.summary = body

    def summary_body(self) -> str | None:
        return self.summary

    def suggestion_block(self, anchor: Anchor, code: str, replaced_lines: int = 1) -> str | None:
        return None

    def read_file(self, path: str, ref: str) -> str | None:
        shown = subprocess.run(  # noqa: S603
            ["git", "show", f"{ref}:{path}"],  # noqa: S607
            cwd=self.repo,
            capture_output=True,
            text=True,
            check=False,
        )
        return shown.stdout if shown.returncode == 0 else None


def build_seeded(case_dir: Path, root: Path) -> tuple[Path, str, str]:
    """`before/` committed on main, then `patch.diff` applied and committed on a branch."""
    repo = root / "repo"
    shutil.copytree(case_dir / "before", repo)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "-b", "feature")
    git(repo, "apply", str(case_dir / "patch.diff"))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "change")
    return repo, base, git(repo, "rev-parse", "HEAD")


def build_aacr(case: AacrCase, root: Path) -> tuple[Path, str, str]:
    repo = root / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "remote", "add", "origin", case.repo_url)
    git(repo, "fetch", "-q", "--no-tags", "--depth=1", "origin", case.base_commit)
    git(repo, "branch", "main", "FETCH_HEAD", "-f")
    git(repo, "fetch", "-q", "--no-tags", "--depth=50", "origin", case.head_commit)
    git(repo, "checkout", "-q", "-b", "feature", case.head_commit)
    return repo, case.base_commit, case.head_commit


def load_seeded() -> list[Case]:
    cases: list[Case] = []
    for case_dir in sorted(p for p in SEEDED.iterdir() if p.is_dir()):
        meta = json.loads((case_dir / "case.json").read_text())
        expected = [Expected.model_validate(e) for e in json.loads((case_dir / "expected.json").read_text())]
        cases.append(Case(case_dir.name, meta["stack"], expected, lambda root, d=case_dir: build_seeded(d, root)))
    return cases


def posted_findings(posted: list[Finding], anchors: dict[str, Anchor]) -> list[Posted]:
    """What the pipeline posted, as scored: inline findings at their anchor, general threads at the agent's line.

    Scanner findings (`forced`) are not the agent's and are left out; a finding anchored on the old side of the diff
    cannot match a bug in the new code, so it is left out too.
    """
    out: list[Posted] = []
    for f in posted:
        if f.forced:
            continue
        anchor = anchors.get(f.id) if f.id is not None else None
        if anchor is None:
            out.append((f, None))
        elif anchor.side is Side.NEW:
            out.append((f.model_copy(update={"path": anchor.path, "line": anchor.line}), anchor.start_line))
    return out


def run_case(case: Case, env: dict[str, str]) -> list[Posted]:
    with tempfile.TemporaryDirectory(prefix="reviewloop-eval-") as tmp:
        root = Path(tmp)
        repo, base, head = case.build(root)
        pr = PullRequest(
            number=1,
            title=case.name,
            body="",
            source_branch="feature",
            target_branch="main",
            head_sha=head,
            base_sha=base,
            is_fork=False,
        )
        vcs = EvalVcs(repo, pr)
        ctx = RunContext(
            vcs=vcs,
            pr=pr,
            event=vcs.event(),
            env=env,
            source_dir=repo,
            workdir=root / "work",
        )
        run_flow(initial_flow(), ctx)
        return posted_findings(ctx.posted, vcs.anchors)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--aacr", type=int, default=0, help="also run this many AACR-Bench PRs")
    ap.add_argument("--only", help="run one case by name")
    ap.add_argument("--out", type=Path, default=Path("eval-metrics.json"))
    ap.add_argument("--check", type=Path, help="baseline.json; exit 1 when a metric drops past the margin")
    args = ap.parse_args(argv)

    cases = load_seeded()
    if args.aacr:
        try:
            subset = load_subset(args.aacr)
        except Exception as exc:  # AACR is informational; a failed download must not fail the seeded run
            print(f"aacr: skipped, dataset not loaded ({type(exc).__name__})", file=sys.stderr)
            subset = []
        cases += [Case(c.name, "aacr", c.expected, lambda root, c=c: build_aacr(c, root)) for c in subset]
    if args.only:
        cases = [c for c in cases if c.name == args.only]
    env = dict(os.environ)
    results: dict[str, Metrics] = {}
    for case in cases:
        try:
            posted = run_case(case, env)
            results[case.name] = score(case.expected, [f for f, _ in posted], starts=[s for _, s in posted])
        except Exception as exc:  # one broken case must not hide the others; it counts as all expected missed
            print(f"{case.name}: failed ({type(exc).__name__})", file=sys.stderr)
            results[case.name] = score(case.expected, [])
        print(f"{case.name}: {results[case.name]}")
    seeded_names = {c.name for c in cases if c.stack != "aacr"}
    total = aggregate([m for n, m in results.items() if n in seeded_names])
    aacr_parts = [m for n, m in results.items() if n not in seeded_names]
    payload: dict[str, object] = {"seeded": asdict(total), "cases": {n: asdict(m) for n, m in results.items()}}
    print(f"seeded: precision {total.precision:.3f} recall {total.recall:.3f}")
    if aacr_parts:
        # Informational only: never part of the floor or the regression check.
        info = aggregate(aacr_parts)
        payload["aacr"] = asdict(info)
        print(f"aacr (informational): precision {info.precision:.3f} recall {info.recall:.3f}")
    args.out.write_text(json.dumps(payload, indent=2) + "\n")
    problems = [f"FLOOR: {p}" for p in below_floor(total)]
    if args.check and args.check.exists():
        base = json.loads(args.check.read_text())["seeded"]
        problems += [f"REGRESSION: {p}" for p in regressions(Metrics(**base), total)]
    for p in problems:
        print(p, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
