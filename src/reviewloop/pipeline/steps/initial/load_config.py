from pathlib import PurePosixPath

from pydantic import ValidationError

from reviewloop.agents.registry import get_adapter
from reviewloop.config import Config, load_config
from reviewloop.errors import ConfigError
from reviewloop.pipeline.context import RunContext
from reviewloop.trackers.registry import get_tracker

CONFIG_FILE = ".reviewloop.yml"
# Set by the GitHub Action and the GitLab component from their inputs (ruling R82).
CONFIG_PATH_ENV = "REVIEWLOOP_CONFIG_PATH"
OVERRIDE_ENV = {"agent": "REVIEWLOOP_AGENT", "model": "REVIEWLOOP_MODEL"}


class LoadConfig:
    """Read `.reviewloop.yml` from the target branch only (spec 6.3); build the agent and tracker it names.

    The agent's credentials are checked here, before pre-review-comment posts "Reviewing...", so a run that cannot
    call its agent fails without first announcing a review."""

    id = "load-config"
    group: str | None = None

    def run(self, ctx: RunContext) -> None:
        path = ctx.env.get(CONFIG_PATH_ENV) or CONFIG_FILE
        parts = PurePosixPath(path.replace("\\", "/"))
        if parts.is_absolute() or ".." in parts.parts:
            raise ConfigError(f"{CONFIG_PATH_ENV} must be a path inside the repository, without '..'")
        ctx.config = self._override(ctx, load_config(ctx.vcs.read_file(path, ctx.pr.target_branch)))
        if ctx.agent is None:
            ctx.agent = get_adapter(ctx.config.agent)
        ctx.agent.check_auth()
        tracker = ctx.config.tracker
        if ctx.tracker is None and tracker is not None:
            ctx.tracker = get_tracker(tracker.type, tracker.url, ctx.env)

    @staticmethod
    def _override(ctx: RunContext, config: Config) -> Config:
        """Apply the agent and model the CI job sets, validated by the same model as the file."""
        update = {field: ctx.env[name] for field, name in OVERRIDE_ENV.items() if ctx.env.get(name)}
        if not update:
            return config
        try:
            return Config.model_validate({**config.model_dump(), **update})
        except ValidationError as e:
            names = ", ".join(OVERRIDE_ENV[f] for f in update)
            raise ConfigError(f"Invalid {names}: {e.errors()[0]['msg']}") from e
