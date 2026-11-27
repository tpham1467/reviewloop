from typing import Any, Literal, cast

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from reviewloop.errors import ConfigError

_SECRET_WORDS = ("token", "secret", "password", "api_key")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PathsConfig(_Strict):
    exclude: list[str] = []


class RuleEntry(_Strict):
    path: str
    file: str


class TrackerConfig(_Strict):
    type: Literal["github", "gitlab", "jira"]
    url: str | None = None
    key_pattern: str | None = None
    require_context: bool = False


class SecurityConfig(_Strict):
    enabled: bool = True
    scanners: list[str] = ["opengrep", "gitleaks"]


class Config(_Strict):
    version: Literal[1] = 1
    agent: Literal["claude-code", "codex", "cursor"] = "claude-code"
    model: str | None = None
    language: str = "en"
    paths: PathsConfig = PathsConfig()
    rules: list[RuleEntry] = []
    tracker: TrackerConfig | None = None
    security: SecurityConfig = SecurityConfig()
    suggestions: bool = True
    fail_on: Literal["none", "critical", "high"] = "none"
    large_diff_lines: int = 3000
    review_drafts: bool = False


def _check_secret_keys(node: object, path: str = "") -> None:
    if isinstance(node, dict):
        for key, value in cast(dict[Any, Any], node).items():
            where = f"{path}.{key}" if path else str(key)
            if any(w in str(key).lower() for w in _SECRET_WORDS):
                raise ConfigError(
                    f"Config key '{where}' looks like a secret; "
                    "secrets are never read from .reviewloop.yml, use a CI variable"
                )
            _check_secret_keys(value, where)
    elif isinstance(node, list):
        for item in cast(list[Any], node):
            _check_secret_keys(item, path)


def load_config(text: str | None) -> Config:
    if text is None:
        return Config()
    try:
        raw: object = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ConfigError(f"Invalid YAML in .reviewloop.yml: {e}") from e
    if raw is None:
        return Config()
    if not isinstance(raw, dict):
        raise ConfigError(".reviewloop.yml must be a mapping at the top level")
    _check_secret_keys(cast(dict[Any, Any], raw))
    try:
        return Config.model_validate(raw)
    except ValidationError as e:
        err = e.errors()[0]
        key = ".".join(str(p) for p in err["loc"])
        raise ConfigError(f"Invalid config at '{key}': {err['msg']}") from e
