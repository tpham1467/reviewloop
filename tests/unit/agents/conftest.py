import json
import subprocess
from pathlib import Path

import pytest
from pydantic import BaseModel

from reviewloop.agents.base import AgentTask

FAKE_AGENT = Path(__file__).resolve().parents[2] / "fixtures" / "agents" / "fake_agent.py"


class Echo(BaseModel):
    message: str
    count: int


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout  # noqa: S603, S607


@pytest.fixture
def fake_agent() -> str:
    return str(FAKE_AGENT)


@pytest.fixture
def source_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "source"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "dev@example.test")
    git(repo, "config", "user.name", "Dev")
    (repo / "app.py").write_text("print('hi')\n")
    git(repo, "add", "app.py")
    git(repo, "commit", "-q", "-m", "first")
    (repo / "app.py").write_text("print('hello')\n")
    git(repo, "commit", "-q", "-am", "second")
    return repo


@pytest.fixture
def make_task(tmp_path: Path):
    def _make(
        mode: str = "good",
        output: object = None,
        step: str = "review",
        timeout_s: int = 30,
        **extra: str,
    ) -> AgentTask:
        workdir = tmp_path / "sandbox"
        (workdir / ".reviewloop" / "out").mkdir(parents=True, exist_ok=True)
        payload = {
            "mode": mode,
            "output": output if output is not None else {"message": "ok", "count": 2},
            "out": f".reviewloop/out/{step}.json",
            **extra,
        }
        (workdir / ".reviewloop" / "fake-agent.json").write_text(json.dumps(payload))
        return AgentTask(
            step=step,
            prompt="Review the change.",
            skills=[],
            workdir=workdir,
            output_schema=Echo,
            timeout_s=timeout_s,
        )

    return _make
