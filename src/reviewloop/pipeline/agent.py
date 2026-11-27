"""Helpers every agent step shares: inputs into the sandbox, one agent call, numbered finding lists."""

import json
import shutil
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from reviewloop.agents.base import AgentTask
from reviewloop.agents.runner import run_with_repair
from reviewloop.diff import changed_lines
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


def numbered(findings: list[Finding]) -> str:
    """Findings as a JSON list whose `id` is the index the agent answers with."""
    items = [
        {"id": i, **f.model_dump(mode="json", include={"path", "line", "severity", "category", "text"}),
         "existing_code": f.existing_code, "cause_code": f.cause_code}
        for i, f in enumerate(findings)
    ]  # fmt: skip
    return json.dumps(items, indent=2)


def run_agent(
    ctx: RunContext, step: str, skills: list[str], prompt: str, schema: type[_O], sandbox: Path | None = None
) -> _O:
    if ctx.agent is None:
        raise AgentStepError(f"step '{step}': no agent configured")
    timeout = min(BASE_TIMEOUT_S + 2 * changed_lines(ctx.reviewable()), MAX_TIMEOUT_S)
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
