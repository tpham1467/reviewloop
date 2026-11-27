from pathlib import Path

import pytest

from reviewloop.agents.base import AgentResult, AgentTask
from reviewloop.agents.claude_code import ClaudeCodeAdapter
from reviewloop.agents.runner import MAX_PREVIOUS_OUTPUT, AgentOutputError, output_path, read_output, run_with_repair
from reviewloop.errors import AgentStepError


class Counting:
    """Real adapter around the fake CLI that records each prompt it is given."""

    def __init__(self, binary: str) -> None:
        self.inner = ClaudeCodeAdapter(binary=binary)
        self.name = self.inner.name
        self.prompts: list[str] = []

    def check_auth(self) -> None:
        self.inner.check_auth()

    def run(self, task: AgentTask) -> AgentResult:
        self.prompts.append(task.prompt)
        return self.inner.run(task)


def test_repair_feeds_validation_errors_back(fake_agent: str, make_task) -> None:
    adapter = Counting(fake_agent)

    output = run_with_repair(adapter, make_task(mode="bad_then_good"))

    assert output.model_dump() == {"message": "ok", "count": 2}
    assert len(adapter.prompts) == 2
    assert adapter.prompts[0] == "Review the change."
    repair = adapter.prompts[1]
    assert repair.startswith("Review the change.")
    assert "failed validation" in repair
    assert "Invalid JSON" in repair
    assert ".reviewloop/out/review.json" in repair


def test_repair_gives_up_after_two(fake_agent: str, make_task) -> None:
    adapter = Counting(fake_agent)

    with pytest.raises(AgentStepError, match=r"step 'review'.*2 repair"):
        run_with_repair(adapter, make_task(mode="bad"))

    assert len(adapter.prompts) == 3


def test_repair_reports_schema_errors(fake_agent: str, make_task) -> None:
    adapter = Counting(fake_agent)

    with pytest.raises(AgentStepError, match="count"):
        run_with_repair(adapter, make_task(output={"message": "ok"}), max_repairs=1)

    assert "count" in adapter.prompts[1]
    assert len(adapter.prompts) == 2


def test_repair_does_not_retry_isolation_failure(fake_agent: str, make_task) -> None:
    adapter = Counting(fake_agent)

    with pytest.raises(AgentStepError, match="planted"):
        run_with_repair(adapter, make_task(mode="extra_write"))

    assert len(adapter.prompts) == 1


def test_run_clears_stale_output_before_each_attempt(fake_agent: str, make_task) -> None:
    task = make_task(mode="none")
    (task.workdir / output_path(task)).write_text('{"message": "stale", "count": 1}')

    with pytest.raises(AgentStepError, match="was not written"):
        run_with_repair(Counting(fake_agent), task, max_repairs=0)


def test_read_output_truncates_long_errors(make_task) -> None:
    task = make_task()
    (task.workdir / output_path(task)).write_text('{"message": ' + '"x' * 5000 + "}")

    with pytest.raises(AgentOutputError) as info:
        read_output(task)

    assert len(info.value.detail) <= 2000


def test_read_output_missing_file(make_task) -> None:
    with pytest.raises(AgentOutputError, match="was not written"):
        read_output(make_task())


def test_output_path_is_relative_to_workdir(make_task) -> None:
    assert output_path(make_task(step="gate")) == Path(".reviewloop/out/gate.json")


def test_repair_shows_previous_output(fake_agent: str, make_task) -> None:
    adapter = Counting(fake_agent)
    raw = '{"message": "half'

    with pytest.raises(AgentStepError):
        run_with_repair(adapter, make_task(mode="bad", raw=raw), max_repairs=1)

    assert f"<previous_output>\n{raw}\n</previous_output>" in adapter.prompts[1]


def test_repair_truncates_previous_output(fake_agent: str, make_task) -> None:
    adapter = Counting(fake_agent)

    with pytest.raises(AgentStepError):
        run_with_repair(adapter, make_task(mode="bad", raw="x" * 30_000), max_repairs=1)

    assert "x" * MAX_PREVIOUS_OUTPUT in adapter.prompts[1]
    assert "x" * (MAX_PREVIOUS_OUTPUT + 1) not in adapter.prompts[1]


def test_validation_errors_never_echo_output(fake_agent: str, make_task) -> None:
    adapter = Counting(fake_agent)
    task = make_task(output={"message": "ok", "count": "ghp_FAKE_LEAK"})

    with pytest.raises(AgentStepError) as info:
        run_with_repair(adapter, task, max_repairs=1)

    assert "ghp_FAKE_LEAK" not in str(info.value)
    assert "count: Input should be a valid integer" in str(info.value)
    error_part = adapter.prompts[1].split("<previous_output>")[0]
    assert "ghp_FAKE_LEAK" not in error_part


def test_stderr_redacted_before_tail_cut(make_task) -> None:
    import sys

    from reviewloop.agents.runner import STDERR_TAIL, run_cli

    token = "ghp_" + "A1b2C3d4E5" * 4
    # The kept tail starts 20 characters before the token's end: cut first, the rest no longer matches a pattern.
    after = STDERR_TAIL - 21
    script = f"import sys; sys.stderr.write('x' * 300 + {token!r} + ' ' + 'y' * {after}); sys.exit(1)"

    with pytest.raises(AgentStepError, match="exit code 1") as caught:
        run_cli(make_task(), [sys.executable, "-c", script], {})

    message = str(caught.value)
    assert "[redacted]" in message
    assert "C3d4E5" not in message
    assert message.endswith("y" * after)
