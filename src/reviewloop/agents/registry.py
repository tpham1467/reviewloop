from collections.abc import Callable

from reviewloop.agents.base import AgentAdapter
from reviewloop.agents.claude_code import ClaudeCodeAdapter
from reviewloop.errors import ConfigError

ADAPTERS: dict[str, Callable[..., AgentAdapter]] = {
    ClaudeCodeAdapter.name: ClaudeCodeAdapter,
}


def get_adapter(name: str) -> AgentAdapter:
    try:
        factory = ADAPTERS[name]
    except KeyError:
        raise ConfigError(f"unknown agent {name!r}; available: {', '.join(sorted(ADAPTERS))}") from None
    return factory()
