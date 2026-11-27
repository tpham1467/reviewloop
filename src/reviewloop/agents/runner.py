"""One isolated CLI run, output validation and the repair loop, shared by every agent adapter."""

import contextlib
import os
import re
import signal
import subprocess
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel, ValidationError

from reviewloop.agents.base import AgentAdapter, AgentResult, AgentTask
from reviewloop.agents.isolation import snapshot, verify_only_output_changed
from reviewloop.errors import AgentStepError

MAX_DETAIL = 2000
MAX_PREVIOUS_OUTPUT = 20_000
STDERR_TAIL = 500
# The step name ends up in a file path and in a CLI permission rule, so keep it to plain characters.
_STEP_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class AgentOutputError(AgentStepError):
    """The output file is missing or does not validate.

    `detail` (validation errors, never the output itself) goes into the message and the repair prompt; `previous`
    is the invalid output, shown to the agent on the repair turn so it can rewrite it.
    """

    def __init__(self, step: str, detail: str, previous: str | None = None) -> None:
        self.detail = detail[:MAX_DETAIL]
        self.previous = previous[:MAX_PREVIOUS_OUTPUT] if previous is not None else None
        super().__init__(f"step '{step}': invalid output: {self.detail}")


def output_path(task: AgentTask) -> Path:
    """Output file of a step, relative to the sandbox."""
    if not _STEP_NAME.match(task.step):
        raise AgentStepError(f"invalid step name {task.step!r}: use lowercase letters, digits, '-' and '_'")
    return Path(".reviewloop") / "out" / f"{task.step}.json"


def _describe(exc: ValidationError) -> str:
    # include_input=False keeps agent output (which may quote secrets it read) out of error messages and logs.
    return "\n".join(
        f"{'.'.join(str(part) for part in error['loc']) or '<root>'}: {error['msg']}"
        for error in exc.errors(include_input=False)
    )


def read_output(task: AgentTask) -> BaseModel:
    rel = output_path(task)
    path = task.workdir / rel
    if not path.is_file():
        raise AgentOutputError(task.step, f"The output file {rel.as_posix()} was not written.")
    raw = path.read_bytes()
    try:
        return task.output_schema.model_validate_json(raw)
    except ValidationError as exc:
        raise AgentOutputError(task.step, _describe(exc), raw.decode(errors="replace")) from exc


def _transcript_path(task: AgentTask) -> Path:
    logs = task.workdir / ".reviewloop" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    n = 1
    while (logs / f"{task.step}-{n}.jsonl").exists():
        n += 1
    return logs / f"{task.step}-{n}.jsonl"


def _kill_group(proc: subprocess.Popen[bytes]) -> None:
    # The CLI runs in its own session, so its process group holds every child it started.
    with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGKILL)
    proc.wait()


def run_cli(task: AgentTask, argv: list[str], env: Mapping[str, str]) -> AgentResult:
    """Run one agent CLI invocation in the sandbox `task.workdir`, the prompt on stdin, and validate its output.

    Raises `AgentOutputError` (repairable) when the output is missing or invalid, and `AgentStepError` for anything
    else, including any change in the sandbox besides the output file. Processes the CLI leaves behind are killed.
    """
    rel = output_path(task)
    (task.workdir / rel).unlink(missing_ok=True)
    transcript = _transcript_path(task)
    before = snapshot(task.workdir)
    start = time.monotonic()
    with transcript.open("wb") as stdout, tempfile.TemporaryFile() as stderr:
        try:
            proc = subprocess.Popen(  # noqa: S603
                argv,
                cwd=task.workdir,
                env=dict(env),
                stdin=subprocess.PIPE,
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            raise AgentStepError(f"step '{task.step}': agent CLI {argv[0]} not found") from exc
        except OSError as exc:
            raise AgentStepError(f"step '{task.step}': agent CLI {argv[0]} could not start: {exc.strerror}") from exc
        try:
            proc.communicate(task.prompt.encode(), timeout=task.timeout_s)
        except subprocess.TimeoutExpired as exc:
            raise AgentStepError(f"step '{task.step}': agent timed out after {task.timeout_s}s") from exc
        finally:
            _kill_group(proc)
        stderr.seek(0)
        err = stderr.read().decode(errors="replace").strip()[-STDERR_TAIL:]
    duration = time.monotonic() - start
    try:
        verify_only_output_changed(before, snapshot(task.workdir), rel, transcript.relative_to(task.workdir))
    except AgentStepError as exc:
        raise AgentStepError(f"step '{task.step}': {exc}") from exc
    if proc.returncode != 0:
        raise AgentStepError(f"step '{task.step}': agent exited with exit code {proc.returncode}: {err}")
    return AgentResult(output=read_output(task), transcript_path=transcript, duration_s=duration)


def _repair_prompt(task: AgentTask, error: AgentOutputError) -> str:
    rel = output_path(task).as_posix()
    previous = (
        f"Its content was:\n<previous_output>\n{error.previous}\n</previous_output>\n\n"
        if error.previous is not None
        else ""
    )
    return (
        f"{task.prompt}\n\n"
        f"Your previous output file {rel} failed validation:\n\n{error.detail}\n\n{previous}"
        f"Rewrite {rel} as valid JSON that matches the required schema. Change no other file."
    )


def run_with_repair(adapter: AgentAdapter, task: AgentTask, max_repairs: int = 2) -> BaseModel:
    """Run a step; on missing or invalid output, feed the errors back for at most `max_repairs` more turns."""
    attempt = task
    for _ in range(max_repairs):
        try:
            return adapter.run(attempt).output
        except AgentOutputError as exc:
            attempt = task.model_copy(update={"prompt": _repair_prompt(task, exc)})
    try:
        return adapter.run(attempt).output
    except AgentOutputError as exc:
        raise AgentStepError(
            f"step '{task.step}': output still invalid after {max_repairs} repair turns: {exc.detail}"
        ) from exc
