import os
import re
import subprocess

from reviewloop.agents.isolation import GIT_ENV
from reviewloop.diff import FileDiff, parse_unified_diff
from reviewloop.errors import ReviewloopError
from reviewloop.models import CoverageStatus, FileCoverage
from reviewloop.output.redact import redact
from reviewloop.pipeline.context import RunContext
from reviewloop.pipeline.git import is_sha, run_git
from reviewloop.stacks import glob_match

_VERSION = re.compile(r"git version (\d+)\.(\d+)")
# `--attr-source` (git 2.40) reads .gitattributes from a given tree instead of the PR head's working tree.
ATTR_SOURCE_VERSION = (2, 40)
# git calls a blob binary when its first 8000 bytes hold a NUL.
BINARY_PROBE_BYTES = 8000
LOCKFILES = frozenset(
    {"package-lock.json", "pnpm-lock.yaml", "yarn.lock", "poetry.lock", "uv.lock", "Cargo.lock", "go.sum",
     "composer.lock", "Gemfile.lock"}
)  # fmt: skip
GENERATED = ("vendor/**", "node_modules/**", "dist/**", "build/**", "**/*.min.js", "**/*.min.css", "**/*.generated.*",
             "**/*_pb2.py")  # fmt: skip
DIFF_ARGS = ("diff", "--no-color", "--no-ext-diff", "--no-textconv", "-M")


def _run(ctx: RunContext, *args: str) -> bytes:
    result = run_git(ctx.source_dir, *args)
    if result.returncode != 0:
        ctx.log.warning("git %s failed: %s", args[0], redact(result.stderr.decode(errors="replace").strip()))
        raise ReviewloopError(
            f"get-diff: git {args[0]} failed; the CI job must check out the PR head and run "
            f"`git fetch --no-tags origin {ctx.pr.target_branch}` before reviewloop"
        )
    return result.stdout


def _git(ctx: RunContext, *args: str) -> str:
    return _run(ctx, *args).decode("utf-8", errors="replace")


def attr_source_supported(ctx: RunContext) -> bool:
    match = _VERSION.match(_git(ctx, "--version"))
    return match is not None and (int(match[1]), int(match[2])) >= ATTR_SOURCE_VERSION


def _has_nul(ctx: RunContext, rev: str, path: str) -> bool:
    """git's binary rule on the blob's first 8000 bytes, without loading the rest of it."""
    with subprocess.Popen(  # noqa: S603
        ["git", "cat-file", "blob", f"{rev}:{path}"],  # noqa: S607
        cwd=ctx.source_dir,
        env={**os.environ, **GIT_ENV},
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    ) as proc:
        assert proc.stdout is not None  # noqa: S101 - stdout=PIPE always sets it
        head = proc.stdout.read(BINARY_PROBE_BYTES)
        proc.kill()  # the rest of the blob is not needed
    return b"\0" in head


def _text_marked_binary(ctx: RunContext, fd: FileDiff, start: str, head: str) -> bool:
    """A file the diff calls binary although neither side's content is (a `-diff` attribute did it)."""
    if not fd.is_binary or fd.status == "deleted":
        return False
    if _has_nul(ctx, head, fd.path):
        return False
    return fd.status == "added" or not _has_nul(ctx, start, fd.old_path or fd.path)


def _diff(ctx: RunContext, start: str, head: str, merge_base: str) -> list[FileDiff]:
    """Diff `start..head` with .gitattributes from the merge-base: no PR commit may hide changes by marking them
    binary, including the previous head a follow-up turn diffs from."""
    if attr_source_supported(ctx):
        return parse_unified_diff(_git(ctx, f"--attr-source={merge_base}", *DIFF_ARGS, start, head))
    diffs = parse_unified_diff(_git(ctx, *DIFF_ARGS, start, head))
    hidden = [fd for fd in diffs if _text_marked_binary(ctx, fd, start, head)]
    if not hidden:
        return diffs
    paths = sorted({p for fd in hidden for p in (fd.path, fd.old_path) if p})
    forced = parse_unified_diff(_git(ctx, "--literal-pathspecs", *DIFF_ARGS, "--text", start, head, "--", *paths))
    by_path = {fd.path: fd for fd in forced}
    return [by_path.get(fd.path, fd) if fd in hidden else fd for fd in diffs]


def exclusion(fd: FileDiff, patterns: list[str]) -> str | None:
    """Why a changed file is not sent to the agent (spec 6.4), or None to review it."""
    name = fd.path.rsplit("/", 1)[-1]
    if fd.is_binary:
        return "binary"
    if fd.status == "deleted":
        return "deleted"
    if fd.status == "renamed" and not fd.hunks:
        return "rename only"
    if fd.mode_only:
        return "mode only"
    if name in LOCKFILES or name.endswith(".lock"):
        return "lockfile"
    if any(glob_match(g, fd.path) for g in GENERATED):
        return "generated or vendored"
    if any(glob_match(g, fd.path) for g in patterns):
        return "excluded by config"
    return None


class GetDiff:
    """Diff `merge-base(base, head)..head` in the CI checkout and mark the files that are not reviewed.

    A follow-up turn diffs from the previous head instead, when force-push-and-lineage kept it as the base.
    """

    id = "get-diff"
    group: str | None = None

    def run(self, ctx: RunContext) -> None:
        base, head = ctx.pr.base_sha, ctx.pr.head_sha
        if not (is_sha(base) and is_sha(head)):
            raise ReviewloopError("get-diff: base and head must be hex commit SHAs")
        merge_base = _git(ctx, "merge-base", base, head).strip()
        start = merge_base
        if ctx.follow_up is not None and ctx.follow_up.diff_base is not None:
            start = ctx.follow_up.diff_base
        ctx.diffs = _diff(ctx, start, head, merge_base)
        ctx.excluded = []
        for fd in ctx.diffs:
            reason = exclusion(fd, ctx.config.paths.exclude)
            if reason is not None:
                ctx.excluded.append(FileCoverage(path=fd.path, status=CoverageStatus.EXCLUDED, reason=reason))
        ctx.coverage = list(ctx.excluded)
