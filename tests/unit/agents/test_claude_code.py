import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from reviewloop.agents import claude_code
from reviewloop.agents.claude_code import ClaudeCodeAdapter
from reviewloop.errors import AgentStepError


def _clear_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith(("ANTHROPIC_", "CLAUDE_CODE_")):
            monkeypatch.delenv(name)


class EnvDump(BaseModel):
    env: list[str]
    home: str
    skills: list[str]


class PromptDump(BaseModel):
    prompt: str


class SettingsDump(BaseModel):
    settings: dict[str, Any]


def _arg(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


def test_claude_code_args_allowlist(make_task) -> None:
    task = make_task(step="review")

    argv = ClaudeCodeAdapter().command(task)

    assert argv[:2] == ["claude", "-p"]
    assert "Review the change." not in argv
    assert _arg(argv, "--output-format") == "stream-json"
    assert "--verbose" in argv
    assert _arg(argv, "--allowedTools") == (
        "Read,Grep,Glob,Skill,Bash(git log:*),Bash(git blame:*),Bash(git show:*),Bash(git diff:*),"
        "Edit(.reviewloop/out/review.json)"
    )
    assert _arg(argv, "--tools") == "Read,Grep,Glob,Skill,Bash,Write"
    disallowed = _arg(argv, "--disallowedTools").split(",")
    assert {"NotebookEdit", "WebFetch", "WebSearch", "Bash(*$*)", "Bash(*--output*)", "Read(//proc/**)"} <= set(
        disallowed
    )
    # git options that read a file outside the repository, and /proc where the model credential lives.
    assert {"Bash(*--no-index*)", "Bash(*--contents*)", "Bash(*/proc/*)"} <= set(disallowed)
    # git blame options that read a named file (through a committed link before the copy dropped links),
    # and the implicit no-index form of git diff.
    assert {
        "Bash(git blame*-S*)",
        "Bash(git blame*--conte*)",
        "Bash(git blame*--con*)",
        "Bash(git blame*--ignore-rev*)",
        "Bash(git diff*/dev/null*)",
    } <= set(disallowed)
    # A bare Edit deny would also block the Write tool, which the agent needs for its output file.
    assert "Edit" not in disallowed
    assert _arg(argv, "--permission-mode") == "dontAsk"
    assert _arg(argv, "--setting-sources") == "user"
    assert "--strict-mcp-config" in argv
    assert "--no-session-persistence" in argv
    assert "--model" not in argv


def test_claude_code_passes_model(make_task) -> None:
    task = make_task().model_copy(update={"model": "sonnet"})

    argv = ClaudeCodeAdapter(binary="/opt/claude").command(task)

    assert argv[0] == "/opt/claude"
    assert _arg(argv, "--model") == "sonnet"


@pytest.mark.parametrize("step", ["../x", "review*", "", "Review"])
def test_claude_code_rejects_unsafe_step_name(make_task, step: str) -> None:
    task = make_task().model_copy(update={"step": step})

    with pytest.raises(AgentStepError, match="step name"):
        ClaudeCodeAdapter().command(task)


def test_check_auth_without_credentials_fails_clearly(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_credentials(monkeypatch)

    with pytest.raises(AgentStepError) as info:
        ClaudeCodeAdapter().check_auth()

    message = str(info.value)
    assert "ANTHROPIC_API_KEY" in message
    assert "CLAUDE_CODE_OAUTH_TOKEN" in message


def test_check_auth_accepts_oauth_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_credentials(monkeypatch)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat-FAKE0000")

    ClaudeCodeAdapter().check_auth()


def test_run_returns_validated_output_and_transcript(fake_agent: str, make_task) -> None:
    task = make_task(output={"message": "done", "count": 3})

    result = ClaudeCodeAdapter(binary=fake_agent).run(task)

    assert result.output.model_dump() == dict(message="done", count=3)
    assert result.transcript_path == task.workdir / ".reviewloop" / "logs" / "review-1.jsonl"
    assert '"type": "result"' in result.transcript_path.read_text()
    assert result.duration_s >= 0


def test_run_scrubs_env_and_uses_fresh_home(
    fake_agent: str, make_task, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_FAKE0000")
    monkeypatch.setenv("REVIEWLOOP_GITLAB_TOKEN", "glpat-FAKE0000")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-FAKE0000")
    skills = tmp_path / "skills"
    (skills / "code-review").mkdir(parents=True)
    (skills / "code-review" / "SKILL.md").write_text("---\nname: code-review\n---\nbody\n")
    monkeypatch.setattr(claude_code, "SKILLS_DIR", skills)
    task = make_task(mode="env").model_copy(update={"output_schema": EnvDump, "skills": ["code-review"]})

    result = ClaudeCodeAdapter(binary=fake_agent).run(task)

    dump = result.output
    assert isinstance(dump, EnvDump)
    assert "GITHUB_TOKEN" not in dump.env
    assert "REVIEWLOOP_GITLAB_TOKEN" not in dump.env
    assert "ANTHROPIC_API_KEY" in dump.env
    assert dump.home != str(Path.home())
    assert not Path(dump.home).exists()
    assert dump.skills == ["code-review"]
    assert not (task.workdir / ".claude").exists()


def test_run_writes_hardened_settings_in_home(fake_agent: str, make_task) -> None:
    task = make_task(mode="settings").model_copy(update={"output_schema": SettingsDump})

    dump = ClaudeCodeAdapter(binary=fake_agent).run(task).output

    assert isinstance(dump, SettingsDump)
    permissions = dump.settings["permissions"]
    assert permissions["blockReadsOutsideWorkingDirectories"] is True
    assert permissions["blockWritesOutsideWorkingDirectories"] is True
    assert permissions["disableBypassPermissionsMode"] == "disable"
    assert dump.settings["claudeMdExcludes"] == ["**/CLAUDE.md", "**/CLAUDE.local.md", "**/.claude/rules/**"]


def test_run_unknown_skill_fails(fake_agent: str, make_task, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(claude_code, "SKILLS_DIR", tmp_path / "skills")
    task = make_task().model_copy(update={"skills": ["nope"]})

    with pytest.raises(AgentStepError, match="nope"):
        ClaudeCodeAdapter(binary=fake_agent).run(task)


def test_run_rejects_extra_write(fake_agent: str, make_task) -> None:
    with pytest.raises(AgentStepError, match=r"review.*planted\.txt"):
        ClaudeCodeAdapter(binary=fake_agent).run(make_task(mode="extra_write"))


def test_run_timeout_fails_step(fake_agent: str, make_task) -> None:
    with pytest.raises(AgentStepError, match=r"review.*timed out after 1s"):
        ClaudeCodeAdapter(binary=fake_agent).run(make_task(mode="sleep", timeout_s=1))


def test_run_nonzero_exit_fails_step(fake_agent: str, make_task) -> None:
    with pytest.raises(AgentStepError, match=r"review.*exit code 1.*fake agent failure"):
        ClaudeCodeAdapter(binary=fake_agent).run(make_task(mode="exit1"))


def test_run_missing_binary_fails_step(make_task, tmp_path: Path) -> None:
    with pytest.raises(AgentStepError, match="not found"):
        ClaudeCodeAdapter(binary=str(tmp_path / "no-such-cli")).run(make_task())


def test_cloud_credentials_pass_only_for_selected_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_credentials(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-FAKE0000")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAFAKE0000")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/fake/key.json")

    assert ClaudeCodeAdapter().environment("/home/agent").keys() >= {"ANTHROPIC_API_KEY", "HOME"}
    assert "AWS_ACCESS_KEY_ID" not in ClaudeCodeAdapter().environment("/home/agent")
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in ClaudeCodeAdapter().environment("/home/agent")

    monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", "1")
    bedrock = ClaudeCodeAdapter().environment("/home/agent")
    assert bedrock["AWS_ACCESS_KEY_ID"] == "AKIAFAKE0000"
    assert bedrock["HOME"] == "/home/agent"
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in bedrock

    monkeypatch.delenv("CLAUDE_CODE_USE_BEDROCK")
    monkeypatch.setenv("CLAUDE_CODE_USE_VERTEX", "1")
    monkeypatch.setenv("CLOUD_ML_REGION", "us-east5")
    vertex = ClaudeCodeAdapter().environment("/home/agent")
    assert vertex["GOOGLE_APPLICATION_CREDENTIALS"] == "/fake/key.json"
    assert vertex["CLOUD_ML_REGION"] == "us-east5"
    assert "AWS_ACCESS_KEY_ID" not in vertex


def test_run_sends_prompt_on_stdin(fake_agent: str, make_task) -> None:
    task = make_task(mode="prompt").model_copy(update={"output_schema": PromptDump})

    dump = ClaudeCodeAdapter(binary=fake_agent).run(task).output

    assert dump == PromptDump(prompt="Review the change.")


def _alive(pid: int) -> bool:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except FileNotFoundError:
        return False
    return stat.rsplit(")", 1)[1].split()[0] != "Z"


def _grandchild(transcript: Path) -> int:
    for line in transcript.read_text().splitlines():
        if "grandchild" in line:
            return int(json.loads(line)["grandchild"])
    raise AssertionError("no grandchild pid in transcript")


def _wait_dead(pid: int) -> bool:
    deadline = time.monotonic() + 5
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    return not _alive(pid)


def test_timeout_kills_process_group(fake_agent: str, make_task) -> None:
    task = make_task(mode="spawn", timeout_s=2)

    with pytest.raises(AgentStepError, match="timed out"):
        ClaudeCodeAdapter(binary=fake_agent).run(task)

    assert _wait_dead(_grandchild(task.workdir / ".reviewloop" / "logs" / "review-1.jsonl"))


def test_run_kills_leftover_processes(fake_agent: str, make_task) -> None:
    result = ClaudeCodeAdapter(binary=fake_agent).run(make_task(mode="spawn_exit"))

    assert _wait_dead(_grandchild(result.transcript_path))


def test_run_unstartable_binary_fails_step(make_task, tmp_path: Path) -> None:
    binary = tmp_path / "claude"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o644)

    with pytest.raises(AgentStepError, match="could not start"):
        ClaudeCodeAdapter(binary=str(binary)).run(make_task())


@pytest.mark.parametrize("value", ["0", "false", ""])
def test_check_auth_ignores_falsy_provider_flag(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    _clear_credentials(monkeypatch)
    monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", value)
    monkeypatch.setenv("CLAUDE_CODE_USE_VERTEX", value)

    with pytest.raises(AgentStepError):
        ClaudeCodeAdapter().check_auth()


@pytest.mark.parametrize("value", ["1", "true", "TRUE"])
def test_check_auth_accepts_truthy_provider_flag(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    _clear_credentials(monkeypatch)
    monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", value)

    ClaudeCodeAdapter().check_auth()


def test_bedrock_passes_only_named_aws_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_credentials(monkeypatch)
    named = [
        "AWS_REGION",
        "AWS_DEFAULT_REGION",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_BEARER_TOKEN_BEDROCK",
    ]
    for name in named:
        monkeypatch.setenv(name, "FAKE0000")
    monkeypatch.setenv("AWS_PROFILE", "deploy")
    monkeypatch.setenv("AWS_CONTAINER_CREDENTIALS_FULL_URI", "http://169.254.170.2/creds")
    monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", "1")

    env = ClaudeCodeAdapter().environment("/home/agent")

    assert {k for k in env if k.startswith("AWS_")} == set(named)
    monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", "0")
    assert not any(k.startswith("AWS_") for k in ClaudeCodeAdapter().environment("/home/agent"))


def test_run_keeps_git_guard_first_on_path_in_every_shell(fake_agent: str, make_task) -> None:
    task = make_task(mode="shell_path").model_copy(update={"output_schema": PromptDump})

    dump = ClaudeCodeAdapter(binary=fake_agent).run(task).output

    # The fake agent resets PATH the way /etc/profile does, then sources CLAUDE_ENV_FILE and resolves git.
    assert isinstance(dump, PromptDump)
    assert dump.prompt.endswith("/bin/git")
    assert dump.prompt.startswith(tempfile.gettempdir())


@pytest.mark.parametrize(
    "rule",
    ["Bash(*/git *)", "Bash(*/git)", "Bash(env *)", "Bash(command *)", "Bash(exec *)", "Bash(*git-*)"],
)
def test_claude_code_denies_git_guard_bypass(make_task, rule: str) -> None:
    # Calling git by path, through a launcher, or as a git-<sub> binary would skip the git guard on PATH.
    disallowed = _arg(ClaudeCodeAdapter().command(make_task()), "--disallowedTools").split(",")

    assert rule in disallowed
