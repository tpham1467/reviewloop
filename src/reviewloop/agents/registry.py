import os
from collections.abc import Callable

from reviewloop.agents.base import AgentAdapter
from reviewloop.agents.claude_code import ClaudeCodeAdapter
from reviewloop.agents.codex import CodexAdapter
from reviewloop.agents.cursor import CursorAdapter
from reviewloop.errors import ConfigError

# An experimental adapter cannot hold its CLI to the spec 8.2 tool set (docs/adr/0002-agent-tool-restriction.md), so
# it is only available when the user opts in.
EXPERIMENTAL_ENV = "REVIEWLOOP_EXPERIMENTAL_AGENTS"

ADAPTERS: dict[str, Callable[..., AgentAdapter]] = {
    ClaudeCodeAdapter.name: ClaudeCodeAdapter,
    CodexAdapter.name: CodexAdapter,
    CursorAdapter.name: CursorAdapter,
}
EXPERIMENTAL = frozenset(name for name, factory in ADAPTERS.items() if getattr(factory, "experimental", False))


def get_adapter(name: str) -> AgentAdapter:
    opted_in = os.environ.get(EXPERIMENTAL_ENV) == "1"
    available = sorted(n for n in ADAPTERS if opted_in or n not in EXPERIMENTAL)
    if name in EXPERIMENTAL and not opted_in:
        raise ConfigError(
            f"agent {name!r} is experimental: it cannot be limited to read tools and git log/blame/show/diff. "
            f"Set {EXPERIMENTAL_ENV}=1 to use it anyway; available: {', '.join(available)}"
        )
    if name not in available:
        raise ConfigError(f"unknown agent {name!r}; available: {', '.join(available)}")
    return ADAPTERS[name]()
