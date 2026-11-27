import os
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from reviewloop.agents import isolation
from reviewloop.agents.codex import CodexAdapter
from reviewloop.errors import AgentStepError

FAKE_AUTH = '{"OPENAI_API_KEY": null, "tokens": {"refresh_token": "rt_FAKE0000"}}'


def _clear_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith(("OPENAI_", "CODEX_", "ANTHROPIC_", "CLAUDE_CODE_", "CURSOR_")):
            monkeypatch.delenv(name)


class EnvDump(BaseModel):
    env: list[str]
    home: str
    skills: list[str]
    path: str


class HomeDump(BaseModel):
    files: dict[str, dict[str, Any]]


class PromptDump(BaseModel):
    prompt: str


def _overrides(argv: list[str]) -> dict[str, str]:
    pairs = [argv[i + 1] for i, arg in enumerate(argv) if arg == "-c"]
    return dict(pair.split("=", 1) for pair in pairs)


AGENT_ENV = {"PATH": "/agent-home/bin:/usr/bin", "HOME": "/agent-home", "CODEX_API_KEY": "sk-FAKE0000"}


def test_codex_args_read_only_sandbox(make_task) -> None:
    argv = CodexAdapter().command(make_task(step="review"), AGENT_ENV)

    assert argv[:2] == ["codex", "exec"]
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    assert argv[argv.index("--output-last-message") + 1] == ".reviewloop/out/review.json"
    assert {"--json", "--ephemeral", "--ignore-user-config", "--ignore-rules"} <= set(argv)
    # The prompt comes on stdin.
    assert argv[-1] == "-"
    assert "Review the change." not in argv
    # Nothing that widens the sandbox, trusts the checkout or skips approvals.
    for flag in ("--dangerously-bypass-approvals-and-sandbox", "--yolo", "--approve-for-me", "--trust", "--add-dir"):
        assert flag not in argv
    overrides = _overrides(argv)
    assert overrides["approval_policy"] == '"never"'
    assert overrides["allow_login_shell"] == "false"
    assert overrides["project_doc_max_bytes"] == "0"
    assert overrides["web_search"] == '"disabled"'
    assert overrides["cli_auth_credentials_store"] == '"file"'
    assert overrides["shell_environment_policy.inherit"] == '"none"'
    # Shell commands get the guard-first PATH and HOME only: never the model credential.
    assert overrides["shell_environment_policy.set"] == '{PATH = "/agent-home/bin:/usr/bin", HOME = "/agent-home"}'
    for feature in ("apps", "multi_agent", "shell_snapshot"):
        assert overrides[f"features.{feature}"] == "false"
    assert "--model" not in argv


def test_codex_passes_model(make_task) -> None:
    task = make_task().model_copy(update={"model": "gpt-5-codex"})

    argv = CodexAdapter(binary="/opt/codex").command(task, AGENT_ENV)

    assert argv[0] == "/opt/codex"
    assert argv[argv.index("--model") + 1] == "gpt-5-codex"


def test_codex_is_experimental() -> None:
    assert CodexAdapter.experimental is True


def test_codex_check_auth_names_both_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_credentials(monkeypatch)

    with pytest.raises(AgentStepError) as info:
        CodexAdapter().check_auth()

    assert "OPENAI_API_KEY" in str(info.value)
    assert "CODEX_AUTH_JSON" in str(info.value)


@pytest.mark.parametrize("var", ["OPENAI_API_KEY", "CODEX_AUTH_JSON"])
def test_codex_check_auth_accepts_either_credential(monkeypatch: pytest.MonkeyPatch, var: str) -> None:
    _clear_credentials(monkeypatch)
    monkeypatch.setenv(var, "sk-FAKE0000")

    CodexAdapter().check_auth()


def test_codex_env_holds_only_its_own_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_credentials(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-FAKE0000")
    monkeypatch.setenv("CODEX_AUTH_JSON", FAKE_AUTH)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-FAKE0000")
    monkeypatch.setenv("CURSOR_API_KEY", "key_FAKE0000")
    monkeypatch.setenv("CODEX_HOME", "/runner/.codex")

    env = CodexAdapter().environment("/home/agent")

    assert env["HOME"] == "/home/agent"
    # codex exec reads an API key only from CODEX_API_KEY, so OPENAI_API_KEY is handed over under that name.
    assert env["CODEX_API_KEY"] == "sk-FAKE0000"
    for var in ("OPENAI_API_KEY", "CODEX_AUTH_JSON", "ANTHROPIC_API_KEY", "CURSOR_API_KEY", "CODEX_HOME"):
        assert var not in env


def test_codex_env_without_api_key_has_no_codex_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_credentials(monkeypatch)
    monkeypatch.setenv("CODEX_AUTH_JSON", FAKE_AUTH)

    assert "CODEX_API_KEY" not in CodexAdapter().environment("/home/agent")


def test_codex_writes_auth_json_with_mode_600(fake_agent: str, make_task, monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_credentials(monkeypatch)
    monkeypatch.setenv("CODEX_AUTH_JSON", FAKE_AUTH)
    task = make_task(mode="home").model_copy(update={"output_schema": HomeDump})

    dump = CodexAdapter(binary=fake_agent).run(task).output

    assert isinstance(dump, HomeDump)
    assert dump.files[".codex/auth.json"] == {"mode": "600", "text": FAKE_AUTH}


def test_codex_without_auth_json_writes_no_auth_file(
    fake_agent: str, make_task, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_credentials(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-FAKE0000")
    task = make_task(mode="home").model_copy(update={"output_schema": HomeDump})

    dump = CodexAdapter(binary=fake_agent).run(task).output

    assert isinstance(dump, HomeDump)
    assert ".codex/auth.json" not in dump.files


def test_codex_auth_json_is_not_in_agent_env(fake_agent: str, make_task, monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_credentials(monkeypatch)
    monkeypatch.setenv("CODEX_AUTH_JSON", FAKE_AUTH)
    task = make_task(mode="env").model_copy(update={"output_schema": EnvDump})

    dump = CodexAdapter(binary=fake_agent).run(task).output

    assert isinstance(dump, EnvDump)
    assert "CODEX_AUTH_JSON" not in dump.env


def test_codex_installs_skills_in_home(
    fake_agent: str, make_task, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    skills = tmp_path / "skills"
    (skills / "code-review").mkdir(parents=True)
    (skills / "code-review" / "SKILL.md").write_text("---\nname: code-review\n---\nbody\n")
    monkeypatch.setattr(isolation, "SKILLS_DIR", skills)
    task = make_task(mode="home").model_copy(update={"output_schema": HomeDump, "skills": ["code-review"]})

    dump = CodexAdapter(binary=fake_agent).run(task).output

    assert isinstance(dump, HomeDump)
    assert ".agents/skills/code-review/SKILL.md" in dump.files


def test_codex_unknown_skill_fails(fake_agent: str, make_task, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(isolation, "SKILLS_DIR", tmp_path / "skills")
    task = make_task().model_copy(update={"skills": ["nope"]})

    with pytest.raises(AgentStepError, match="nope"):
        CodexAdapter(binary=fake_agent).run(task)


def test_codex_prompt_says_final_message_is_the_output(fake_agent: str, make_task) -> None:
    task = make_task(mode="prompt").model_copy(update={"output_schema": PromptDump})

    dump = CodexAdapter(binary=fake_agent).run(task).output

    assert isinstance(dump, PromptDump)
    assert dump.prompt.startswith("Review the change.")
    assert "final message" in dump.prompt
    assert ".reviewloop/out/review.json" in dump.prompt
