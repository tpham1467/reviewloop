from pathlib import Path
from typing import Protocol

from pydantic import BaseModel


class AgentTask(BaseModel):
    step: str
    prompt: str
    skills: list[str]
    workdir: Path
    output_schema: type[BaseModel]
    timeout_s: int
    model: str | None = None


class AgentResult(BaseModel):
    output: BaseModel
    transcript_path: Path
    duration_s: float


class AgentAdapter(Protocol):
    name: str

    def check_auth(self) -> None: ...

    def run(self, task: AgentTask) -> AgentResult:
        """One CLI invocation; the repair loop lives in agents/runner.py."""
        ...
