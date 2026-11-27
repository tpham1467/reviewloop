"""Codex adapter (experimental): `codex exec` in a read-only sandbox, never asking for approval.

Experimental because Codex cannot restrict shell commands to the spec 8.2 allowlist: in the read-only sandbox every
command runs without approval (`cat`, `ls`, `rg`, ...), and execpolicy rules only decide what may run outside the
sandbox. Its reads are not limited to the copy either. Writes are blocked by the sandbox, network is off, and the
post-run snapshot check still fails any change. See docs/adr/0002-agent-tool-restriction.md.

Codex writes the output file itself (`--output-last-message`), so the model's final message is the JSON output and the
sandbox stays read-only. Credentials: OPENAI_API_KEY (handed to `codex exec` as CODEX_API_KEY, the only API key variable
it reads) or CODEX_AUTH_JSON, written to `$HOME/.codex/auth.json` in the throwaway HOME. A refreshed token is not saved
back, so an auth.json login must be re-saved by the user when its refresh token rotates.
"""

import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path

from reviewloop.agents.base import AgentResult, AgentTask
from reviewloop.agents.isolation import install_git_wrapper, install_skills, scrub_env
from reviewloop.agents.runner import output_path, run_cli
from reviewloop.errors import AgentStepError

AUTH_JSON_VAR = "CODEX_AUTH_JSON"
API_KEY_VAR = "OPENAI_API_KEY"
# Off: connectors, subagents, and the shell snapshot that would copy a login shell's environment into commands.
DISABLED_FEATURES = ("apps", "multi_agent", "shell_snapshot")


def _toml(value: str) -> str:
    # A JSON string is a valid TOML basic string.
    return json.dumps(value)


class CodexAdapter:
    name = "codex"
    experimental = True

    def __init__(self, binary: str = "codex") -> None:
        self.binary = binary

    def check_auth(self) -> None:
        if os.environ.get(API_KEY_VAR) or os.environ.get(AUTH_JSON_VAR):
            return
        raise AgentStepError(f"Codex has no credential: set {API_KEY_VAR} or {AUTH_JSON_VAR}")

    def environment(self, home: str) -> dict[str, str]:
        env = scrub_env([])
        env["HOME"] = home
        key = os.environ.get(API_KEY_VAR)
        if key:
            env["CODEX_API_KEY"] = key
        return env

    def command(self, task: AgentTask, env: Mapping[str, str]) -> list[str]:
        shell_env = f"{{PATH = {_toml(env['PATH'])}, HOME = {_toml(env['HOME'])}}}"
        overrides = {
            "approval_policy": _toml("never"),
            # A login shell would run /etc/profile, which can put the real git ahead of the guard on PATH.
            "allow_login_shell": "false",
            # AGENTS.md stays in the copy as data, but is not loaded as instructions the PR author controls.
            "project_doc_max_bytes": "0",
            "web_search": _toml("disabled"),
            "cli_auth_credentials_store": _toml("file"),
            # Shell commands get PATH and HOME only, never the model credential.
            "shell_environment_policy.inherit": _toml("none"),
            "shell_environment_policy.set": shell_env,
            **{f"features.{feature}": "false" for feature in DISABLED_FEATURES},
        }
        argv = [
            self.binary,
            "exec",
            "--sandbox",
            "read-only",
            "--json",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--output-last-message",
            output_path(task).as_posix(),
        ]
        for key, value in overrides.items():
            argv += ["-c", f"{key}={value}"]
        if task.model:
            argv += ["--model", task.model]
        return [*argv, "-"]

    def run(self, task: AgentTask) -> AgentResult:
        rel = output_path(task).as_posix()
        prompt = (
            f"{task.prompt}\n\nYour final message is saved as {rel}: make it only that JSON document, with no other "
            "text and no code fence. Do not try to write any file; the workspace is read-only."
        )
        task = task.model_copy(update={"prompt": prompt})
        with tempfile.TemporaryDirectory(prefix="reviewloop-home-") as home:
            install_skills(task.step, task.skills, Path(home) / ".agents" / "skills")
            auth = os.environ.get(AUTH_JSON_VAR)
            if auth:
                config = Path(home) / ".codex"
                config.mkdir(mode=0o700)
                fd = os.open(config / "auth.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w") as handle:
                    handle.write(auth)
            bin_dir = install_git_wrapper(Path(home), task.workdir)
            env = self.environment(home)
            env["PATH"] = os.pathsep.join([str(bin_dir), env.get("PATH", os.defpath)])
            return run_cli(task, self.command(task, env), env)
