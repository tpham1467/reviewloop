import json
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from reviewloop import cli, platform
from reviewloop.config import Config, load_config
from reviewloop.errors import AgentStepError, ReviewloopError
from reviewloop.models import Command, CoverageStatus, Event, EventKind, FileCoverage, Finding, Severity, User
from reviewloop.pipeline import flows
from reviewloop.pipeline.context import RunContext
from reviewloop.pipeline.steps.initial import load_config as load_config_step
from reviewloop.vcs.bitbucket_cloud import BitbucketCloudAdapter
from reviewloop.vcs.bitbucket_dc import BitbucketDCAdapter
from reviewloop.vcs.github import GitHubAdapter
from reviewloop.vcs.gitlab import GitLabAdapter

runner = CliRunner()
READER = User(id="5", username="reader")
FAKE_TOKEN = "ghp_" + "F" * 36


class Step:
    """A flow step that runs `action` on the context."""

    def __init__(self, step_id: str, action: Any = None) -> None:
        self.id = step_id
        self.group: str | None = None
        self.action = action
        self.ran = False

    def run(self, ctx: RunContext) -> None:
        self.ran = True
        if self.action is not None:
            self.action(ctx)


@pytest.fixture
def ci(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_vcs: Any) -> Any:
    """Run the CLI in a temp checkout with `vcs` as the detected platform; agents cannot be built unless a test
    replaces `agents`."""
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    monkeypatch.chdir(checkout)

    class Ci:
        def __init__(self) -> None:
            self.vcs = make_vcs()
            self.agents: list[Any] = []

        def invoke(self, *args: str, env: dict[str, str] | None = None) -> Any:
            return runner.invoke(cli.app, list(args), env={"RUNNER_TEMP": str(tmp_path), **(env or {})})

    state = Ci()

    def no_agent(name: str) -> Any:
        if not state.agents:
            raise AssertionError("no agent may be built")
        return state.agents.pop(0)

    monkeypatch.setattr(cli, "detect_platform", lambda env: state.vcs)
    monkeypatch.setattr(load_config_step, "get_adapter", no_agent)
    return state


def with_event(vcs: Any, event: Event, can_write: bool = True) -> Any:
    vcs.event = lambda: event
    vcs.can_write = lambda user: can_write
    return vcs


def test_ignored_event_exits_zero_fast(tmp_path: Path) -> None:
    # A real process over a real adapter: a GitLab push pipeline is ignored without any network call.
    env = {
        "PATH": os.environ["PATH"],
        "GITLAB_CI": "true",
        "CI_SERVER_URL": "https://gitlab.example.test",
        "REVIEWLOOP_GITLAB_TOKEN": "glpat-FAKE",
        "CI_PROJECT_ID": "1",
        "CI_PIPELINE_SOURCE": "push",
        "HTTPS_PROXY": "http://127.0.0.1:9",
    }
    start = time.monotonic()
    result = subprocess.run(
        [sys.executable, "-c", "from reviewloop.cli import app; app()", "run"],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert time.monotonic() - start < 2
    assert "ignored" in result.stderr
    assert list(tmp_path.iterdir()) == []


def test_ignored_event_builds_no_agent_and_posts_nothing(ci: Any) -> None:
    with_event(ci.vcs, Event(kind=EventKind.IGNORED))
    result = ci.invoke("run")
    assert result.exit_code == 0
    assert ci.vcs.summaries == [] and ci.vcs.general == []


def test_command_from_reader_ignored(ci: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    with_event(ci.vcs, Event(kind=EventKind.COMMAND, command=Command.REVIEW, actor=READER, pr_number=7), False)
    monkeypatch.setattr(flows, "initial_flow", lambda: pytest.fail("flow must not run"))
    result = ci.invoke("run")
    assert result.exit_code == 0
    assert "no write access" in result.stderr
    assert ci.vcs.summaries == [] and ci.vcs.general == []


def test_command_without_actor_ignored(ci: Any) -> None:
    with_event(ci.vcs, Event(kind=EventKind.COMMAND, command=Command.HELP, pr_number=7))
    assert ci.invoke("run").exit_code == 0
    assert ci.vcs.general == []


def test_fork_pr_skipped_on_open(ci: Any, make_vcs: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    ci.vcs = with_event(make_vcs(is_fork=True), Event(kind=EventKind.OPENED, pr_number=7))
    monkeypatch.setattr(flows, "initial_flow", lambda: pytest.fail("flow must not run"))
    result = ci.invoke("run")
    assert result.exit_code == 0
    assert "fork" in result.stderr
    assert ci.vcs.summaries == []


def test_review_command_runs_on_fork(ci: Any, make_vcs: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    ci.vcs = with_event(make_vcs(is_fork=True), Event(kind=EventKind.COMMAND, command=Command.REVIEW, actor=READER))
    step = Step("only")
    monkeypatch.setattr(flows, "initial_flow", lambda: [step])
    assert ci.invoke("run").exit_code == 0
    assert step.ran


def test_help_posts_command_list(ci: Any) -> None:
    with_event(ci.vcs, Event(kind=EventKind.COMMAND, command=Command.HELP, actor=READER, pr_number=7))
    result = ci.invoke("run")
    assert result.exit_code == 0
    [body] = ci.vcs.general
    for command in ("/reviewloop review", "/reviewloop recheck", "/reviewloop help"):
        assert command in body
    # The bot's own comment must never parse as a command.
    assert not body.lstrip().startswith("/reviewloop")
    assert ci.vcs.summaries == []


HIGH = Finding(path="app.py", line=3, severity=Severity.HIGH, category="null-safety", text="x", existing_code="y")


def finish(findings: list[Finding], coverage: list[FileCoverage]) -> Any:
    def act(ctx: RunContext) -> None:
        ctx.findings = findings
        ctx.coverage = coverage

    return act


@pytest.mark.parametrize(
    ("fail_on", "coverage_status", "code"),
    [("none", CoverageStatus.REVIEWED, 0), ("high", CoverageStatus.REVIEWED, 1), ("none", CoverageStatus.FAILED, 2)],
)
def test_exit_code_propagates(
    ci: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fail_on: str, coverage_status: CoverageStatus, code: int
) -> None:
    with_event(ci.vcs, Event(kind=EventKind.OPENED, pr_number=7))
    coverage = [FileCoverage(path="app.py", status=coverage_status, reason="r")]

    def config(ctx: RunContext) -> None:
        ctx.config = Config(fail_on=fail_on)

    steps = [Step("load-config", config), Step("post-comment", finish([HIGH], coverage)), Step("summary")]
    monkeypatch.setattr(flows, "initial_flow", lambda: steps)
    out = tmp_path / "out"

    result = ci.invoke("run", "--output-dir", str(out))

    assert result.exit_code == code, result.stderr
    doc = json.loads((out / "reviewloop.json").read_text())
    assert [f["severity"] for f in doc["findings"]] == ["high"]
    sarif = json.loads((out / "reviewloop.sarif").read_text())
    assert sarif["runs"][0]["results"][0]["level"] == "error"


def test_artifacts_need_post_comment(ci: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    with_event(ci.vcs, Event(kind=EventKind.OPENED, pr_number=7))
    monkeypatch.setattr(flows, "initial_flow", lambda: [Step("get-pr-info")])
    assert ci.invoke("run").exit_code == 0
    assert not (tmp_path / "checkout" / "reviewloop.json").exists()


def raise_(exc: BaseException) -> Any:
    def act(*args: Any) -> None:
        raise exc

    return act


@pytest.mark.parametrize("exc", [ReviewloopError("VCS answered HTTP 502"), AgentStepError("agent timed out")])
def test_infra_error_exits_2(ci: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, exc: Exception) -> None:
    with_event(ci.vcs, Event(kind=EventKind.OPENED, pr_number=7))
    clean = Step("clean")
    clean.group = "finally"
    monkeypatch.setattr(flows, "initial_flow", lambda: [Step("post-comment", raise_(exc)), clean])

    result = ci.invoke("run")

    assert result.exit_code == 2
    assert str(exc) in result.stderr
    assert clean.ran
    # post-comment started, so the artifacts list the run's state even though it failed.
    assert (tmp_path / "checkout" / "reviewloop.json").is_file()


def test_vcs_error_before_flow_exits_2(ci: Any) -> None:
    ci.vcs.event = raise_(ReviewloopError("cannot read event payload"))
    result = ci.invoke("run")
    assert result.exit_code == 2
    assert "cannot read event payload" in result.stderr


def test_unexpected_exception_exits_2_without_message(ci: Any) -> None:
    ci.vcs.event = raise_(RuntimeError(f"Authorization: token {FAKE_TOKEN} internal detail"))
    result = ci.invoke("run")
    assert result.exit_code == 2
    assert "RuntimeError" in result.stderr
    assert "internal detail" not in result.output and "internal detail" not in result.stderr
    assert "Traceback" not in result.stderr


def test_logs_are_redacted(ci: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    with_event(ci.vcs, Event(kind=EventKind.OPENED, pr_number=7))

    def leak(ctx: RunContext) -> None:
        ctx.log.info("agent said %s", FAKE_TOKEN)
        try:
            raise ValueError(FAKE_TOKEN)
        except ValueError:
            ctx.log.exception("while parsing")
        raise ReviewloopError(f"bad token {FAKE_TOKEN}")

    monkeypatch.setattr(flows, "initial_flow", lambda: [Step("leak", leak)])

    result = ci.invoke("run")

    assert result.exit_code == 2
    assert FAKE_TOKEN not in result.stderr
    assert result.stderr.count("[redacted]") >= 3
    assert "while parsing" in result.stderr


def test_github_ignored_event_reads_no_pull_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The adapter's constructor fetches the repository (PyGithub is not lazy); nothing else may be called.
    monkeypatch.chdir(tmp_path)
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"action": "closed", "pull_request": {"number": 7}}))
    env = {**github_env(tmp_path), "RUNNER_TEMP": str(tmp_path)}
    with patch("reviewloop.vcs.github.Github") as gh:
        result = runner.invoke(cli.app, ["run"], env=env)
    assert result.exit_code == 0, result.stderr
    assert "ignored" in result.stderr
    calls = [c[0] for c in gh.return_value.mock_calls]
    assert calls == ["get_repo"]
    assert gh.return_value.get_repo.return_value.mock_calls == []


def test_workdir_failure_exits_2_without_traceback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli.app, ["run"], env={"RUNNER_TEMP": str(tmp_path / "missing"), "GITHUB_TOKEN": FAKE_TOKEN})
    assert result.exit_code == 2
    assert "FileNotFoundError" in result.stderr
    assert "Traceback" not in result.output and "Traceback" not in result.stderr
    assert FAKE_TOKEN not in result.output and FAKE_TOKEN not in result.stderr


def test_interrupt_exits_2_and_removes_workdir(ci: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    with_event(ci.vcs, Event(kind=EventKind.OPENED, pr_number=7))

    def cancel(ctx: RunContext) -> None:
        assert ctx.workdir.is_dir()
        # What a CI cancel sends; the CLI turns it into an interrupt.
        signal.raise_signal(signal.SIGTERM)

    monkeypatch.setattr(flows, "initial_flow", lambda: [Step("cancelled", cancel)])
    before = signal.getsignal(signal.SIGTERM)

    result = ci.invoke("run")

    assert result.exit_code == 2
    assert "interrupted" in result.stderr
    assert [p for p in tmp_path.iterdir() if p.name.startswith("reviewloop-")] == []
    assert signal.getsignal(signal.SIGTERM) is before


def test_artifact_failure_keeps_flow_error(ci: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    with_event(ci.vcs, Event(kind=EventKind.OPENED, pr_number=7))
    monkeypatch.setattr(flows, "initial_flow", lambda: [Step("post-comment", raise_(ReviewloopError("VCS down")))])
    (tmp_path / "blocker").write_text("a file, not a directory")

    result = ci.invoke("run", "--output-dir", str(tmp_path / "blocker"))

    assert result.exit_code == 2
    assert "could not write artifacts" in result.stderr
    assert "VCS down" in result.stderr


def test_library_logs_redacted(ci: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    with_event(ci.vcs, Event(kind=EventKind.OPENED, pr_number=7))

    def noisy(ctx: RunContext) -> None:
        lib = logging.getLogger("urllib3.connectionpool")
        lib.warning("retrying with %s", FAKE_TOKEN)
        lib.debug("debug %s", "hidden-debug-line")

    monkeypatch.setattr(flows, "initial_flow", lambda: [Step("noisy", noisy)])

    result = ci.invoke("run")

    assert result.exit_code == 0, result.stderr
    assert "urllib3.connectionpool: retrying with [redacted]" in result.stderr
    assert "hidden-debug-line" not in result.stderr
    assert FAKE_TOKEN not in result.stderr


def test_redact_filter_survives_bad_format() -> None:
    # pytest's capture handler re-raises format errors, so the filter is tested on its own record.
    record = logging.LogRecord("lib", logging.WARNING, __file__, 1, "bad %s %s " + FAKE_TOKEN, ("x",), None)
    assert cli._Redact().filter(record)  # pyright: ignore[reportPrivateUsage]
    assert record.getMessage() == "bad %s %s [redacted]"


def test_check_auth_before_any_comment(ci: Any, make_agent: Any) -> None:
    calls: list[str] = []
    with_event(ci.vcs, Event(kind=EventKind.OPENED, pr_number=7))
    summaries = ci.vcs.upsert_summary
    ci.vcs.upsert_summary = lambda body: (calls.append(body.splitlines()[1]), summaries(body))
    agent = make_agent()
    agent.check_auth = lambda: (calls.append("check_auth"), raise_(ReviewloopError("ANTHROPIC_API_KEY not set"))())
    ci.agents = [agent]

    result = ci.invoke("run")

    assert result.exit_code == 2
    assert "ANTHROPIC_API_KEY not set" in result.stderr
    # The failure is reported, but no "Reviewing..." comment was posted before the agent could run.
    assert calls == ["check_auth", "Review failed: ANTHROPIC_API_KEY not set"]
    assert ci.vcs.general == []


def test_manual_review_targets_pr_and_skips_permission_check(ci: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    ci.vcs.event = raise_(AssertionError("a manual run has no CI event"))
    ci.vcs.can_write = raise_(AssertionError("the token holder ran it"))
    seen: list[Event] = []
    monkeypatch.setattr(flows, "initial_flow", lambda: [Step("record", lambda ctx: seen.append(ctx.event))])

    assert ci.invoke("review", "--pr", "7").exit_code == 0

    assert seen == [Event(kind=EventKind.COMMAND, command=Command.REVIEW, actor=User(id="99", username="bot"),
                          pr_number=7)]  # fmt: skip


def test_recheck_until_follow_up_exists(ci: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    with_event(ci.vcs, Event(kind=EventKind.COMMAND, command=Command.RECHECK, actor=READER, pr_number=7))
    monkeypatch.setattr(flows, "follow_up_flow", list)
    for args in (["run"], ["recheck", "--pr", "7"]):
        result = ci.invoke(*args)
        assert result.exit_code == 0
        assert "follow-up not available yet" in result.stderr
    assert ci.vcs.summaries == [] and ci.vcs.general == []


def test_recheck_runs_follow_up_flow(ci: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    with_event(ci.vcs, Event(kind=EventKind.COMMAND, command=Command.RECHECK, actor=READER, pr_number=7))
    step = Step("follow-up")
    monkeypatch.setattr(flows, "follow_up_flow", lambda: [step])
    monkeypatch.setattr(flows, "initial_flow", lambda: pytest.fail("recheck runs the follow-up flow"))
    assert ci.invoke("run").exit_code == 0
    assert step.ran


def test_workdir_under_runner_temp(ci: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    with_event(ci.vcs, Event(kind=EventKind.OPENED, pr_number=7))
    seen: list[RunContext] = []
    monkeypatch.setattr(flows, "initial_flow", lambda: [Step("record", seen.append)])
    assert ci.invoke("run").exit_code == 0
    [ctx] = seen
    assert ctx.workdir.parent == tmp_path and ctx.workdir.name.startswith("reviewloop-")
    assert ctx.source_dir == tmp_path / "checkout"


# --- platform detection ---


def github_env(tmp_path: Path) -> dict[str, str]:
    return {"GITHUB_ACTIONS": "true", "GITHUB_TOKEN": FAKE_TOKEN, "GITHUB_REPOSITORY": "o/r",
            "GITHUB_EVENT_PATH": str(tmp_path / "event.json"), "GITHUB_EVENT_NAME": "pull_request"}  # fmt: skip


GITLAB_ENV = {"GITLAB_CI": "true", "CI_SERVER_URL": "https://gitlab.example.test", "REVIEWLOOP_GITLAB_TOKEN": "x",
              "CI_PROJECT_ID": "1"}  # fmt: skip
CLOUD_ENV = {"BITBUCKET_BUILD_NUMBER": "3", "BITBUCKET_WORKSPACE": "w", "BITBUCKET_REPO_SLUG": "r",
             "REVIEWLOOP_BITBUCKET_TOKEN": "x"}  # fmt: skip
DC_ENV = {"JENKINS_URL": "https://jenkins.example.test", "BB_PR_ID": "4", "BB_URL": "https://bb.example.test",
          "REVIEWLOOP_BITBUCKET_TOKEN": "x", "BB_PROJECT": "P", "BB_REPO": "r"}  # fmt: skip


@pytest.mark.parametrize(
    ("name", "kind"),
    [("github", GitHubAdapter), ("gitlab", GitLabAdapter), ("bitbucket-cloud", BitbucketCloudAdapter),
     ("bitbucket-dc", BitbucketDCAdapter)],
)  # fmt: skip
def test_detect_platform_each(tmp_path: Path, name: str, kind: type) -> None:
    env = {"github": github_env(tmp_path), "gitlab": GITLAB_ENV, "bitbucket-cloud": CLOUD_ENV, "bitbucket-dc": DC_ENV}
    with patch("reviewloop.vcs.github.Github"):
        assert isinstance(platform.detect_platform(env[name]), kind)
    assert platform.platform_name(env[name]) == name


def test_jenkins_without_pr_id_is_not_bitbucket_dc() -> None:
    assert platform.platform_name({"JENKINS_URL": "https://jenkins.example.test"}) is None


def test_unknown_ci_raises() -> None:
    with pytest.raises(ReviewloopError) as err:
        platform.detect_platform({"CI": "true"})
    for system in ("GitHub Actions", "GitLab CI", "Bitbucket Pipelines", "Bitbucket Data Center"):
        assert system in str(err.value)


def test_missing_platform_variable_named() -> None:
    env = {k: v for k, v in GITLAB_ENV.items() if k != "REVIEWLOOP_GITLAB_TOKEN"}
    with pytest.raises(ReviewloopError, match="REVIEWLOOP_GITLAB_TOKEN"):
        platform.detect_platform(env)


@pytest.mark.parametrize(
    ("env", "key"),
    [(GITLAB_ENV, "CI_MERGE_REQUEST_IID"), (CLOUD_ENV, "PR_ID"), ({"JENKINS_URL": "https://j.test"}, "BB_PR_ID")],
)
def test_for_pull_request_points_adapter_at_pr(tmp_path: Path, env: dict[str, str], key: str) -> None:
    assert platform.for_pull_request(env, 12, tmp_path)[key] == "12"


def test_for_pull_request_github_writes_event(tmp_path: Path) -> None:
    env = platform.for_pull_request({**github_env(tmp_path), "GITHUB_EVENT_NAME": "workflow_dispatch"}, 12, tmp_path)
    with patch("reviewloop.vcs.github.Github"):
        adapter = platform.detect_platform(env)
    assert adapter.event().pr_number == 12


def test_for_pull_request_unknown_ci_unchanged(tmp_path: Path) -> None:
    assert platform.for_pull_request({"CI": "1"}, 12, tmp_path) == {"CI": "1"}


# --- init and version ---


def test_init_writes_default_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli.app, ["init", "--platform", "gitlab"])
    assert result.exit_code == 0, result.output
    text = (tmp_path / ".reviewloop.yml").read_text()
    assert load_config(text) == Config()
    assert "GIT_DEPTH: 0" in result.stdout
    assert "ghcr.io/tpham1467/reviewloop:0" in result.stdout

    (tmp_path / ".reviewloop.yml").write_text("language: vi\n")
    refused = runner.invoke(cli.app, ["init", "--platform", "gitlab"])
    assert refused.exit_code == 2
    assert "--force" in refused.stderr
    assert (tmp_path / ".reviewloop.yml").read_text() == "language: vi\n"

    assert runner.invoke(cli.app, ["init", "--platform", "gitlab", "--force"]).exit_code == 0
    assert load_config((tmp_path / ".reviewloop.yml").read_text()) == Config()


def test_init_github_snippet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli.app, ["init"], env={"GITHUB_ACTIONS": "true"})
    assert result.exit_code == 0
    out = result.stdout
    for text in ("persist-credentials: false", "fetch-depth: 0", "uses: tpham1467/reviewloop@v0"):
        assert text in out
    assert 'fetch --no-tags origin "refs/pull/${PR_NUMBER}/head"' in out
    assert "PR_NUMBER: ${{ github.event.pull_request.number || github.event.issue.number }}" in out
    # The token goes to the fetch command only; checkout keeps no credential.
    assert out.count("GH_TOKEN") == 2 and "extraheader" in out
    # Concurrency sits under the job, after its `if`, so a plain comment cannot cancel a review.
    assert out.index("concurrency:") > out.index("if: >-") > out.index("jobs:")


def test_init_gitlab_snippet_fetches_mr_head(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    out = runner.invoke(cli.app, ["init", "--platform", "gitlab"]).stdout
    assert '"refs/merge-requests/${MR_IID}/head"' in out
    assert "CI_MERGE_REQUEST_IID" in out and "TRIGGER_PAYLOAD" in out


@pytest.mark.parametrize("name", ["bitbucket-cloud", "bitbucket-dc"])
def test_init_bitbucket_snippets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli.app, ["init", "--platform", name])
    assert result.exit_code == 0
    assert "ghcr.io/tpham1467/reviewloop:0" in result.stdout and "reviewloop run" in result.stdout


def test_init_unknown_platform_asks_for_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    for key in ("GITHUB_ACTIONS", "GITLAB_CI", "BITBUCKET_BUILD_NUMBER", "JENKINS_URL"):
        monkeypatch.delenv(key, raising=False)
    result = runner.invoke(cli.app, ["init"])
    assert result.exit_code == 2
    assert "--platform" in result.stderr
    assert not (tmp_path / ".reviewloop.yml").exists()


def test_version() -> None:
    result = runner.invoke(cli.app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == f"reviewloop {cli.package_version()}"
    assert cli.package_version()[0].isdigit()
