"""Command line: `reviewloop run|review|recheck|init|version` (spec 6.1 and 6.4)."""

import logging
import os
import shutil
import sys
import tempfile
from collections.abc import Mapping
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Annotated

import typer
import yaml

from reviewloop import __version__
from reviewloop.cli_snippets import SNIPPETS
from reviewloop.config import Config
from reviewloop.errors import ReviewloopError
from reviewloop.models import Command, Event, EventKind
from reviewloop.output.json_out import write_json
from reviewloop.output.redact import redact
from reviewloop.output.sarif import write_sarif
from reviewloop.output.summary import exit_code
from reviewloop.pipeline import flows
from reviewloop.pipeline.context import RunContext
from reviewloop.pipeline.engine import Step, run_flow
from reviewloop.pipeline.steps.initial.clean import KEEP_ENV
from reviewloop.pipeline.steps.initial.load_config import CONFIG_FILE
from reviewloop.platform import Platform, detect_platform, for_pull_request, platform_name

INFRA_ERROR = 2
HANDLER_NAME = "reviewloop-cli"
# Steps after which the run's findings are final enough to publish as artifacts.
POSTING_STEPS = frozenset({"post-comment", "post-follow-up-comments"})
# A distribution rename is pending; either name is this package.
DISTRIBUTIONS = ("reviewloop-ai", "reviewloop")
HELP_TEXT = """\
reviewloop commands (write access required), posted as a pull request comment:

- `/reviewloop review`: review the whole pull request again.
- `/reviewloop recheck`: re-check the open findings against new commits and your replies.
- `/reviewloop help`: show this list.
"""

log = logging.getLogger("reviewloop")
app = typer.Typer(no_args_is_help=True, add_completion=False, help="AI code review in your own CI.")

OutputDir = Annotated[Path, typer.Option("--output-dir", help="Where to write reviewloop.json and reviewloop.sarif.")]
PrNumber = Annotated[int, typer.Option("--pr", min=1, help="Pull request number.")]


class _Redact(logging.Filter):
    """Every log line leaves through `redact`, including tracebacks."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = None
        if record.exc_info:
            record.exc_text = redact(logging.Formatter().formatException(record.exc_info))
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
        return True


def _setup_logging() -> None:
    # A second run in one process (tests) replaces the handler, so it writes to the current stderr.
    for handler in [h for h in log.handlers if h.get_name() == HANDLER_NAME]:
        log.removeHandler(handler)
    handler = logging.StreamHandler(sys.stderr)
    handler.set_name(HANDLER_NAME)
    handler.addFilter(_Redact())
    handler.setFormatter(logging.Formatter("reviewloop %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)


class _Tracked:
    """Records that a step started, so the CLI knows whether the run got as far as posting."""

    def __init__(self, step: Step, started: set[str]) -> None:
        self.step = step
        self.id = step.id
        self.group = step.group
        self.started = started

    def run(self, ctx: RunContext) -> None:
        self.started.add(self.id)
        self.step.run(ctx)


def package_version() -> str:
    for name in DISTRIBUTIONS:
        try:
            return version(name)
        except PackageNotFoundError:
            continue
    return __version__


def _write_artifacts(ctx: RunContext, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "reviewloop.json", ctx.findings, ctx.coverage)
    write_sarif(output_dir / "reviewloop.sarif", ctx.findings)


def _dispatch(env: Mapping[str, str], workdir: Path, output_dir: Path, manual: tuple[Command, int] | None) -> int:
    if manual is not None:
        env = for_pull_request(env, manual[1], workdir)
    vcs = detect_platform(env)
    if manual is None:
        event = vcs.event()
    else:
        # The token holder started this run by hand, so no permission check applies.
        event = Event(kind=EventKind.COMMAND, command=manual[0], actor=vcs.bot_identity(), pr_number=manual[1])
    if event.kind is EventKind.IGNORED:
        log.info("event ignored; nothing to do")
        return 0
    if event.kind is EventKind.COMMAND and manual is None and (event.actor is None or not vcs.can_write(event.actor)):
        log.info("command ignored: the commenter has no write access")
        return 0
    if event.command is Command.HELP:
        vcs.post_general(HELP_TEXT)
        return 0

    pr = vcs.pull_request()
    if event.kind is EventKind.OPENED and pr.is_fork:
        log.info("fork pull request skipped; a user with write access can comment /reviewloop review")
        return 0
    steps = flows.follow_up_flow() if event.command is Command.RECHECK else flows.initial_flow()
    if not steps:
        log.info("recheck: follow-up not available yet")
        return 0

    ctx = RunContext(vcs=vcs, pr=pr, event=event, env=env, source_dir=Path.cwd(), workdir=workdir)
    started: set[str] = set()
    try:
        run_flow([_Tracked(step, started) for step in steps], ctx)
    finally:
        if started & POSTING_STEPS:
            _write_artifacts(ctx, output_dir)
    return exit_code(ctx.findings, ctx.coverage, ctx.config.fail_on)


def _execute(output_dir: Path, manual: tuple[Command, int] | None = None) -> None:
    _setup_logging()
    env = dict(os.environ)
    base = env.get("RUNNER_TEMP") or env.get("CI_BUILDS_DIR") or None
    workdir = Path(tempfile.mkdtemp(prefix="reviewloop-", dir=base))
    try:
        code = _dispatch(env, workdir, output_dir, manual)
    except ReviewloopError as exc:  # AgentStepError and ConfigError too
        log.error("%s", exc)
        code = INFRA_ERROR
    except Exception as exc:
        # The message may carry a token or a response body; the type is enough to report.
        log.error("unexpected error (%s)", type(exc).__name__)
        code = INFRA_ERROR
    finally:
        if env.get(KEEP_ENV) != "1":
            shutil.rmtree(workdir, ignore_errors=True)
    raise typer.Exit(code)


@app.command()
def run(output_dir: OutputDir = Path()) -> None:
    """Handle the CI event: review an opened pull request or run a /reviewloop command."""
    _execute(output_dir)


@app.command()
def review(pr: PrNumber, output_dir: OutputDir = Path()) -> None:
    """Review pull request N now (a manual `/reviewloop review`)."""
    _execute(output_dir, (Command.REVIEW, pr))


@app.command()
def recheck(pr: PrNumber, output_dir: OutputDir = Path()) -> None:
    """Re-check the open findings of pull request N (a manual `/reviewloop recheck`)."""
    _execute(output_dir, (Command.RECHECK, pr))


@app.command()
def init(
    platform: Annotated[
        Platform | None, typer.Option("--platform", help="CI system; detected from the environment when omitted.")
    ] = None,
    force: Annotated[bool, typer.Option("--force", help=f"Overwrite an existing {CONFIG_FILE}.")] = False,
) -> None:
    """Write a default .reviewloop.yml and print the CI configuration to add."""
    chosen = platform or platform_name(os.environ)
    if chosen is None:
        typer.echo("cannot detect the CI system; pass --platform", err=True)
        raise typer.Exit(INFRA_ERROR)
    path = Path(CONFIG_FILE)
    if path.exists() and not force:
        typer.echo(f"{CONFIG_FILE} exists; pass --force to overwrite it", err=True)
        raise typer.Exit(INFRA_ERROR)
    defaults: str = yaml.safe_dump(Config().model_dump(mode="json"), sort_keys=False)  # pyright: ignore[reportUnknownMemberType]
    path.write_text(f"# reviewloop configuration (defaults). Never put secrets here.\n{defaults}", encoding="utf-8")
    typer.echo(f"wrote {CONFIG_FILE}; add this to your CI configuration:\n", err=True)
    typer.echo(SNIPPETS[chosen])


@app.command("version")
def show_version() -> None:
    """Print the reviewloop version."""
    typer.echo(f"reviewloop {package_version()}")
