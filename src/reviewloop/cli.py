"""Command line: `reviewloop run|review|recheck|init|version` (spec 6.1 and 6.4)."""

import logging
import os
import shutil
import signal
import sys
import tempfile
from collections.abc import Mapping
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from types import FrameType
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
from reviewloop.pipeline.engine import run_flow
from reviewloop.pipeline.steps.initial.clean import KEEP_ENV
from reviewloop.pipeline.steps.initial.load_config import CONFIG_FILE
from reviewloop.platform import Platform, detect_platform, for_pull_request, platform_name

INFRA_ERROR = 2
HANDLER_NAME = "reviewloop-cli"
# The distribution name (ruling R70); `reviewloop` on PyPI is another project, so it is never looked up.
DISTRIBUTION = "reviewloop-ai"
HELP_TEXT = """\
reviewloop commands (write access required), posted as a pull request comment:

- `/reviewloop review`: review the whole pull request again.
- `/reviewloop recheck`: re-check the open findings against new commits and your replies.
- `/reviewloop help`: show this list.
"""

log = logging.getLogger("reviewloop")
# Pretty exceptions off: with show_locals (the default before Typer 0.23) a crash would print `os.environ`.
app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="AI code review in your own CI.",
    pretty_exceptions_enable=False,
    pretty_exceptions_show_locals=False,
)

OutputDir = Annotated[Path, typer.Option("--output-dir", help="Where to write reviewloop.json and reviewloop.sarif.")]
PrNumber = Annotated[int, typer.Option("--pr", min=1, help="Pull request number.")]


class _Redact(logging.Filter):
    """Every log line leaves through `redact`, including tracebacks."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except (TypeError, ValueError):  # a bad `%` call in some library must not break logging
            message = str(record.msg)
        record.msg = redact(message)
        record.args = None
        if record.exc_info:
            record.exc_text = redact(logging.Formatter().formatException(record.exc_info))
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
        return True


def _setup_logging() -> None:
    """One redacting handler on the root logger: reviewloop's records at INFO, third-party libraries' at WARNING."""
    root = logging.getLogger()
    # A second run in one process (tests) replaces the handler, so it writes to the current stderr.
    for old in [h for h in root.handlers if h.get_name() == HANDLER_NAME]:
        root.removeHandler(old)
    handler = logging.StreamHandler(sys.stderr)
    handler.set_name(HANDLER_NAME)
    handler.addFilter(_Redact())
    handler.setFormatter(logging.Formatter("reviewloop %(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)
    root.setLevel(logging.WARNING)
    log.setLevel(logging.INFO)


def _terminate(signum: int, frame: FrameType | None) -> None:
    # CI cancels a job with SIGTERM; as an interrupt it unwinds through the clean step and the workdir removal.
    raise KeyboardInterrupt


def package_version() -> str:
    try:
        return version(DISTRIBUTION)
    except PackageNotFoundError:
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
    # A manual run (`review`/`recheck --pr`) builds its own event and skips both checks: the token holder ran it.
    if event.kind is EventKind.COMMAND and manual is None:
        # The bot's own notes fire the comment webhook too; one quoting a command must not start another run.
        # Adapters normalize user ids the same way in `event()` and `bot_identity()`, so equality is enough.
        if event.actor is not None and event.actor.id == vcs.bot_identity().id:
            log.info("command ignored: posted by the reviewloop bot")
            return 0
        if event.actor is None or not vcs.can_write(event.actor):
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
    ctx = RunContext(vcs=vcs, pr=pr, event=event, env=env, source_dir=Path.cwd(), workdir=workdir)
    try:
        run_flow(steps, ctx)
    except BaseException:
        if ctx.posting_started:
            try:
                _write_artifacts(ctx, output_dir)
            except Exception as exc:
                # The flow's error is the one to report.
                log.error("could not write artifacts (%s)", type(exc).__name__)
        raise
    if ctx.posting_started:
        _write_artifacts(ctx, output_dir)
    return exit_code(ctx.findings, ctx.coverage, ctx.config.fail_on)


def _execute(output_dir: Path, manual: tuple[Command, int] | None = None) -> None:
    _setup_logging()
    previous = signal.signal(signal.SIGTERM, _terminate)
    workdir: Path | None = None
    try:
        env = dict(os.environ)
        base = env.get("RUNNER_TEMP") or env.get("CI_BUILDS_DIR") or None
        workdir = Path(tempfile.mkdtemp(prefix="reviewloop-", dir=base))
        code = _dispatch(env, workdir, output_dir, manual)
    except ReviewloopError as exc:  # AgentStepError and ConfigError too
        log.error("%s", exc)
        code = INFRA_ERROR
    except KeyboardInterrupt:
        log.error("interrupted")
        code = INFRA_ERROR
    except Exception as exc:
        # The message may carry a token or a response body; the type is enough to report.
        log.error("unexpected error (%s)", type(exc).__name__)
        code = INFRA_ERROR
    finally:
        signal.signal(signal.SIGTERM, previous)
        if workdir is not None and os.environ.get(KEEP_ENV) != "1":
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
