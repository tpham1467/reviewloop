import os
from pathlib import Path

import pytest
from pydantic import BaseModel

from reviewloop.agents.registry import ADAPTERS, EXPERIMENTAL_ENV, get_adapter
from reviewloop.errors import AgentStepError, ConfigError

CREDENTIAL_PREFIXES = ("ANTHROPIC_", "CLAUDE_CODE_", "AWS_", "CLOUD_ML_", "GOOGLE_", "OPENAI_", "CODEX_", "CURSOR_")


class EnvDump(BaseModel):
    env: list[str]
    home: str
    skills: list[str]
    path: str


def test_registry_has_every_milestone_1_agent() -> None:
    assert sorted(ADAPTERS) == ["claude-code", "codex", "cursor"]


@pytest.mark.parametrize("name", sorted(ADAPTERS))
def test_adapter_contract(fake_agent: str, name: str, make_task, monkeypatch: pytest.MonkeyPatch) -> None:
    for var in list(os.environ):
        if var.startswith(CREDENTIAL_PREFIXES):
            monkeypatch.delenv(var)
    # Experimental adapters are in the contract too.
    monkeypatch.setenv(EXPERIMENTAL_ENV, "1")

    adapter = get_adapter(name)
    assert adapter.name == name
    with pytest.raises(AgentStepError):
        adapter.check_auth()

    fake = ADAPTERS[name](binary=fake_agent)
    result = fake.run(make_task(output={"message": "contract", "count": 1}))
    assert result.output.model_dump() == {"message": "contract", "count": 1}

    with pytest.raises(AgentStepError, match="planted"):
        fake.run(make_task(mode="extra_write", extra_path="src-planted.txt"))


@pytest.mark.parametrize("name", sorted(ADAPTERS))
def test_adapter_contract_env_has_no_vcs_tokens(
    fake_agent: str, name: str, make_task, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_FAKE0000")
    monkeypatch.setenv("GITLAB_TOKEN", "glpat-FAKE0000")
    monkeypatch.setenv("REVIEWLOOP_GITLAB_TOKEN", "glpat-FAKE0000")
    monkeypatch.setenv("REVIEWLOOP_JIRA_TOKEN", "FAKE0000")
    task = make_task(mode="env").model_copy(update={"output_schema": EnvDump})

    dump = ADAPTERS[name](binary=fake_agent).run(task).output

    assert isinstance(dump, EnvDump)
    assert "GITHUB_TOKEN" not in dump.env
    assert "GITLAB_TOKEN" not in dump.env
    assert not any(var.startswith("REVIEWLOOP_") for var in dump.env)
    assert dump.path.split(os.pathsep)[0] == str(Path(dump.home) / "bin")


@pytest.mark.parametrize("name", sorted(ADAPTERS))
def test_adapter_contract_git_is_the_wrapper(fake_agent: str, name: str, make_task) -> None:
    result = ADAPTERS[name](binary=fake_agent).run(make_task(mode="git"))

    assert result.output.model_dump() == {"message": "reviewloop: git option not allowed: --version", "count": 2}


def test_get_adapter_unknown_name() -> None:
    with pytest.raises(ConfigError, match="claude-code"):
        get_adapter("nope")


@pytest.mark.parametrize("value", [None, "0", "", "yes"])
def test_experimental_adapter_needs_opt_in(monkeypatch: pytest.MonkeyPatch, value: str | None) -> None:
    if value is None:
        monkeypatch.delenv(EXPERIMENTAL_ENV, raising=False)
    else:
        monkeypatch.setenv(EXPERIMENTAL_ENV, value)

    for name in ("codex", "cursor"):
        with pytest.raises(ConfigError, match=rf"{name}.*experimental.*{EXPERIMENTAL_ENV}=1"):
            get_adapter(name)
    assert get_adapter("claude-code").name == "claude-code"


def test_experimental_adapter_with_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(EXPERIMENTAL_ENV, "1")

    assert get_adapter("codex").name == "codex"
    assert get_adapter("cursor").name == "cursor"


def test_unknown_name_lists_only_available_adapters(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(EXPERIMENTAL_ENV, raising=False)

    with pytest.raises(ConfigError) as info:
        get_adapter("nope")

    assert "codex" not in str(info.value)
    assert "cursor" not in str(info.value)
    assert "claude-code" in str(info.value)
