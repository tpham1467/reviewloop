import json
import os
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from reviewloop.agents import isolation
from reviewloop.agents.cursor import CursorAdapter
from reviewloop.errors import AgentStepError


def _clear_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith(("OPENAI_", "CODEX_", "ANTHROPIC_", "CLAUDE_CODE_", "CURSOR_")):
            monkeypatch.delenv(name)


class HomeDump(BaseModel):
    files: dict[str, dict[str, Any]]


def test_cursor_args(make_task) -> None:
    argv = CursorAdapter().command(make_task(step="review"))

    assert argv == ["cursor-agent", "-p", "--output-format", "stream-json", "--trust", "--sandbox", "disabled"]
    # The prompt comes on stdin; nothing that runs every action or approves MCP servers.
    assert "Review the change." not in argv


def test_cursor_passes_model(make_task) -> None:
    task = make_task().model_copy(update={"model": "sonnet-4"})

    argv = CursorAdapter(binary="/opt/cursor-agent").command(task)

    assert argv[0] == "/opt/cursor-agent"
    assert argv[argv.index("--model") + 1] == "sonnet-4"


def test_cursor_is_experimental() -> None:
    # Cursor drops `VAR=...` prefixes and assignments when it matches the allowlist, so it cannot hold shell commands.
    assert CursorAdapter.experimental is True


def test_cursor_check_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_credentials(monkeypatch)
    with pytest.raises(AgentStepError, match="CURSOR_API_KEY"):
        CursorAdapter().check_auth()

    monkeypatch.setenv("CURSOR_API_KEY", "key_FAKE0000")
    CursorAdapter().check_auth()


def test_cursor_env_holds_only_its_own_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_credentials(monkeypatch)
    monkeypatch.setenv("CURSOR_API_KEY", "key_FAKE0000")
    monkeypatch.setenv("CURSOR_API_ENDPOINT", "https://evil.example")
    monkeypatch.setenv("CURSOR_CONFIG_DIR", "/runner/cursor")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-FAKE0000")
    monkeypatch.setenv("SHELL", "/bin/bash")

    env = CursorAdapter().environment("/home/agent")

    assert env["CURSOR_API_KEY"] == "key_FAKE0000"
    assert env["HOME"] == "/home/agent"
    for var in ("CURSOR_API_ENDPOINT", "CURSOR_CONFIG_DIR", "OPENAI_API_KEY", "SHELL"):
        assert var not in env


def test_cursor_writes_hardened_config_in_home(fake_agent: str, make_task) -> None:
    task = make_task(mode="home").model_copy(update={"output_schema": HomeDump})

    dump = CursorAdapter(binary=fake_agent).run(task).output

    assert isinstance(dump, HomeDump)
    config = json.loads(dump.files[".cursor/cli-config.json"]["text"])
    assert config["approvalMode"] == "allowlist"
    assert config["sandbox"] == {"mode": "disabled", "readBoundary": "workspace"}
    permissions = config["permissions"]
    assert permissions["allow"] == ["Shell(git log)", "Shell(git blame)", "Shell(git show)", "Shell(git diff)"]
    deny = set(permissions["deny"])
    # git by path or through a launcher would skip the git guard; `$` expansion could print the key.
    assert {"Shell(*/git)", "Shell(env)", "Shell(command)", "Shell(exec)"} <= deny
    # These two also denied real paths that contain `git-` or end in `/git`.
    assert not {"Shell(*/git *)", "Shell(*git-*)"} & deny
    assert {"Shell(*$*)", "Shell(*/proc/*)"} <= deny
    # Files outside the copy that hold VCS or registry credentials on a runner.
    assert {"Read(/proc/*)", "Read(*/.git/config)", "Read(*/.git-credentials)", "Read(*/.netrc)"} <= deny
    assert {"WebFetch(*)", "Mcp(*:*)"} <= deny


def test_cursor_installs_skills_in_home(
    fake_agent: str, make_task, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    skills = tmp_path / "skills"
    (skills / "code-review").mkdir(parents=True)
    (skills / "code-review" / "SKILL.md").write_text("---\nname: code-review\n---\nbody\n")
    monkeypatch.setattr(isolation, "SKILLS_DIR", skills)
    task = make_task(mode="home").model_copy(update={"output_schema": HomeDump, "skills": ["code-review"]})

    dump = CursorAdapter(binary=fake_agent).run(task).output

    assert isinstance(dump, HomeDump)
    assert ".cursor/skills/code-review/SKILL.md" in dump.files


def test_cursor_rejects_extra_write(fake_agent: str, make_task) -> None:
    with pytest.raises(AgentStepError, match=r"review.*planted\.txt"):
        CursorAdapter(binary=fake_agent).run(make_task(mode="extra_write"))
