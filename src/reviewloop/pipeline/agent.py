"""Helpers every agent step shares: inputs into the sandbox, one agent call, numbered finding lists."""

import json
import shutil
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from reviewloop.agents.base import AgentTask
from reviewloop.agents.runner import run_with_repair
from reviewloop.diff import FileDiff, changed_lines
from reviewloop.errors import AgentStepError
from reviewloop.models import Finding
from reviewloop.pipeline.context import RunContext

_O = TypeVar("_O", bound=BaseModel)
BASE_TIMEOUT_S = 300
MAX_TIMEOUT_S = 1800


def write_input(sandbox: Path, rel: str, content: str) -> None:
    """Write an agent input under `<sandbox>/.reviewloop/`; done before the agent's run snapshot is taken."""
    path = sandbox / ".reviewloop" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


_PREFIX = {"add": "+", "del": "-", "ctx": " "}


def render_diff(fd: FileDiff) -> str:
    head = f"--- {fd.old_path or fd.path}\n+++ {fd.path}\n"
    hunks = [
        f"@@ -{h.old_start} +{h.new_start} @@\n" + "".join(f"{_PREFIX[ln.kind]}{ln.text}\n" for ln in h.lines)
        for h in fd.hunks
    ]
    return head + "".join(hunks)


def write_diff(sandbox: Path, diffs: list[FileDiff], excluded: Mapping[str, str | None]) -> None:
    """The diff index and one diff file per file not in `excluded` (path to reason) under `.reviewloop/diff/`."""
    index = ["# Diff index", "", "| File | Status | Input |", "| --- | --- | --- |"]
    for fd in diffs:
        reason = excluded.get(fd.path)
        where = f"excluded: {reason}" if reason else f"`.reviewloop/diff/files/{fd.path}.diff`"
        index.append(f"| `{fd.path}` | {fd.status} | {where} |")
        if reason is None:
            write_input(sandbox, f"diff/files/{fd.path}.diff", render_diff(fd))
    write_input(sandbox, "diff/index.md", "\n".join(index) + "\n")


Reader = Callable[[str], str | None]


def sandbox_reader(sandbox: Path) -> Reader:
    """Read a file of the PR head by repository path; None for a missing file or a path outside the sandbox."""
    root = sandbox.resolve()

    def read(path: str) -> str | None:
        # The sandbox is the PR head with links checked out as plain files, so no read leaves it.
        target = (root / path).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            return None
        return target.read_text(encoding="utf-8", errors="replace")

    return read


def numbered(findings: list[Finding]) -> str:
    """Findings as a JSON list whose `id` is the index the agent answers with."""
    items = [
        {"id": i, **f.model_dump(mode="json", include={"path", "line", "severity", "category", "text"}),
         "existing_code": f.existing_code, "cause_code": f.cause_code}
        for i, f in enumerate(findings)
    ]  # fmt: skip
    return json.dumps(items, indent=2)


def run_agent(
    ctx: RunContext,
    step: str,
    skills: list[str],
    prompt: str,
    schema: type[_O],
    sandbox: Path | None = None,
    lines: int | None = None,
) -> _O:
    """One agent call; the timeout scales with `lines` changed lines, by default the whole reviewable diff."""
    if ctx.agent is None:
        raise AgentStepError(f"step '{step}': no agent configured")
    size = changed_lines(ctx.reviewable()) if lines is None else lines
    timeout = min(BASE_TIMEOUT_S + 2 * size, MAX_TIMEOUT_S)
    task = AgentTask(
        step=step,
        prompt=prompt,
        skills=list(dict.fromkeys([*skills, "output-contract"])),
        workdir=sandbox or ctx.sandbox,
        output_schema=schema,
        timeout_s=timeout,
        model=ctx.config.model,
    )
    output = run_with_repair(ctx.agent, task)
    if not isinstance(output, schema):
        raise AgentStepError(f"step '{step}': output has the wrong type {type(output).__name__}")
    return output


SECURITY_SANDBOX = "security"
OVERVIEW_SANDBOX = "overview"


def branch_sandbox(ctx: RunContext, name: str) -> Path:
    """The sandbox copy an agent step of the parallel review group runs in (ruling R32).

    The post-run check fails a run whose sandbox changed besides its own output, so two agents running at the same
    time cannot share one sandbox. build-context makes the copies before the group starts.
    """
    return ctx.workdir / f"sandbox-{name}"


def copy_sandbox(ctx: RunContext, name: str) -> None:
    shutil.copytree(ctx.sandbox, branch_sandbox(ctx, name), symlinks=True)
