class ReviewloopError(Exception):
    """Base error; the CLI exits with code 2."""


class AgentStepError(ReviewloopError):
    """An agent step failed or returned unusable output."""


class ConfigError(ReviewloopError):
    """Invalid or missing configuration."""
