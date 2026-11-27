"""Cursor adapter (experimental): `cursor-agent -p` in allowlist mode, allowlist of four git commands.

Experimental because Cursor cannot hold shell commands to the allowlist. Its matcher drops `VAR=value` prefixes and
standalone assignments, so `PATH=/usr/bin git diff --no-index ...` or `GIT_EXTERNAL_DIFF=... git log -p --ext-diff`
matches `Shell(git log)`/`Shell(git diff)` and runs past the git guard; and it replays a login-shell state, which can
reset PATH ahead of the guard. In print mode it still rejects shell commands, MCP tools and web fetches the allowlist
does not cover. Its own sandbox stays off: with it on, any command that fits the sandbox would run without being on
the allowlist. Cursor cannot limit edits to one file, so the post-run snapshot check is what enforces writes.

Credentials: CURSOR_API_KEY only. HOME is a fresh temporary directory per run, so a `cursor-agent login` session is not
used. Cursor loads AGENTS.md and CLAUDE.md from the copy as rules; they stay, as data the review may read, and the
output contract tells the agent to treat repository text as data. See docs/adr/0002-agent-tool-restriction.md.
"""

import json
import os
import tempfile
from pathlib import Path

from reviewloop.agents.base import AgentResult, AgentTask
from reviewloop.agents.isolation import install_git_wrapper, install_skills, scrub_env
from reviewloop.agents.runner import run_cli
from reviewloop.errors import AgentStepError

API_KEY_VAR = "CURSOR_API_KEY"
GIT_COMMANDS = ("git log", "git blame", "git show", "git diff")
# Deny wins over allow. These are an outer layer only (see the module docstring). git by path or through a launcher
# would skip the git guard; `$` or backtick expansion and /proc could print the model credential. With Cursor's sandbox
# off its Read tool is not held to the workspace (`readBoundary` applies to sandboxed commands only), so the Read denies
# name runner files that hold VCS or registry credentials outside the copy. They match the path as written, so they
# are best effort; see the ADR.
DENY = (
    "Shell(*/git)",
    "Shell(env)",
    "Shell(command)",
    "Shell(exec)",
    "Shell(*$*)",
    "Shell(*`*)",
    "Shell(*/proc/*)",
    "Read(/proc/*)",
    "Read(*/.git/config)",
    "Read(*/.git/modules/*)",
    "Read(*/.git-credentials)",
    "Read(*/.config/gh/*)",
    "Read(*/.config/glab-cli/*)",
    "Read(*/.netrc)",
    "Read(*/.ssh/*)",
    "Read(*/.docker/config.json)",
    "WebFetch(*)",
    "Mcp(*:*)",
)
# User config in the throwaway HOME. The copy's `.cursor/cli.json` is removed by `prepare_sandbox`.
CONFIG = {
    "version": 1,
    "editor": {"vimMode": False},
    "approvalMode": "allowlist",
    "autoAcceptWebSearch": False,
    "sandbox": {"mode": "disabled", "readBoundary": "workspace"},
    "permissions": {"allow": [f"Shell({cmd})" for cmd in GIT_COMMANDS], "deny": list(DENY)},
}


class CursorAdapter:
    name = "cursor"
    experimental = True

    def __init__(self, binary: str = "cursor-agent") -> None:
        self.binary = binary

    def check_auth(self) -> None:
        if not os.environ.get(API_KEY_VAR):
            raise AgentStepError(f"Cursor has no credential: set {API_KEY_VAR}")

    def environment(self, home: str) -> dict[str, str]:
        env = scrub_env([API_KEY_VAR])
        env["HOME"] = home
        return env

    def command(self, task: AgentTask) -> list[str]:
        # `--trust` stores the trust mark under the throwaway HOME; without it print mode refuses an unknown workspace.
        argv = [self.binary, "-p", "--output-format", "stream-json", "--trust", "--sandbox", "disabled"]
        if task.model:
            argv += ["--model", task.model]
        return argv

    def run(self, task: AgentTask) -> AgentResult:
        argv = self.command(task)
        with tempfile.TemporaryDirectory(prefix="reviewloop-home-") as home:
            config = Path(home) / ".cursor"
            install_skills(task.step, task.skills, config / "skills")
            config.mkdir(exist_ok=True)
            (config / "cli-config.json").write_text(json.dumps(CONFIG))
            bin_dir = install_git_wrapper(Path(home), task.workdir)
            env = self.environment(home)
            env["PATH"] = os.pathsep.join([str(bin_dir), env.get("PATH", os.defpath)])
            return run_cli(task, argv, env)
