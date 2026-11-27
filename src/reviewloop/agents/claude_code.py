"""Claude Code adapter: `claude -p` restricted to read tools, four git commands and one output file.

Credentials come from environment variables only. HOME is a fresh temporary directory per run, so file-based auth
does not work: no `~/.claude` login, no AWS profile or `~/.aws` files, no Google application-default credentials
file under HOME. Use ANTHROPIC_API_KEY, CLAUDE_CODE_OAUTH_TOKEN, Bedrock keys in env, or Vertex with
GOOGLE_APPLICATION_CREDENTIALS pointing at a key file outside HOME.

Besides the allow rules below, Claude Code runs its built-in read-only shell commands (ls, cat, grep, find, read-only
git, ...) without a prompt in every permission mode. They read inside the sandbox, which has no symlinks, and
`blockReadsOutsideWorkingDirectories` plus the post-run snapshot check bound the rest.

The real-CLI smoke run must confirm that `/usr/bin/git diff a /abs/x` is refused (git by path skips the guard).
"""

import json
import os
import shlex
import tempfile
from pathlib import Path

from reviewloop.agents.base import AgentResult, AgentTask
from reviewloop.agents.isolation import install_git_wrapper, install_skills, scrub_env
from reviewloop.agents.runner import output_path, run_cli
from reviewloop.errors import AgentStepError

# Model credentials only. `ANTHROPIC_*` also covers gateway settings such as ANTHROPIC_BASE_URL and
# ANTHROPIC_VERTEX_PROJECT_ID. Cloud credentials pass only when their provider is selected, so a runner's AWS or
# Google credentials for other jobs never reach an agent that uses an API key.
BASE_CREDENTIALS = ("ANTHROPIC_*", "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX")
BEDROCK_CREDENTIALS = (
    "AWS_REGION",
    "AWS_DEFAULT_REGION",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_BEARER_TOKEN_BEDROCK",
)
VERTEX_CREDENTIALS = ("CLOUD_ML_REGION", "VERTEX_REGION_*", "GOOGLE_APPLICATION_CREDENTIALS")
_TOKEN_VARS = ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_AUTH_TOKEN")
_TRUTHY = ("1", "true", "yes", "on")


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUTHY


# Built-in tools the session has at all; everything else (Edit, NotebookEdit, WebFetch, Agent, ...) is absent.
TOOLS = "Read,Grep,Glob,Skill,Bash,Write"
GIT_COMMANDS = ("git log", "git blame", "git show", "git diff")
# Deny wins over allow. A bare `Edit` deny is not used: it also blocks Write, which the output file needs.
# These denies are an outer layer only; the git guard installed by `install_git_wrapper` is what enforces the git
# allowlist (ruling R17), because git accepts abbreviated and grouped options that patterns cannot enumerate.
# `--output` makes git log/show/diff write a file. `--no-index`, a `/dev/null` operand (implicit no-index),
# `--contents` (and its abbreviations), `-S` and `--ignore-revs-file` make git diff/blame read a named file. `$`
# expansion or /proc could print the model credential into the transcript.
DISALLOWED = (
    "NotebookEdit",
    "WebFetch",
    "WebSearch",
    "Bash(*--output*)",
    "Bash(*--no-index*)",
    "Bash(*--contents*)",
    "Bash(*$*)",
    "Bash(*/proc/*)",
    "Bash(git blame*-S*)",
    "Bash(git blame*--conte*)",
    "Bash(git blame*--con*)",
    "Bash(git blame*--ignore-rev*)",
    "Bash(git diff*/dev/null*)",
    # git by path, through a launcher, or as a git-<sub> binary would skip the git guard first on PATH.
    "Bash(*/git *)",
    "Bash(*/git)",
    "Bash(env *)",
    "Bash(command *)",
    "Bash(exec *)",
    "Bash(*git-*)",
    "Read(//proc/**)",
)
# User settings in the throwaway HOME. Project and local settings of the checkout are never loaded, because the
# checkout is untrusted and its `.claude/settings.json` could add hooks or allow rules.
SETTINGS = {
    "disableAllHooks": True,
    # CLAUDE.md files and rules in the checkout are instructions the PR author controls; AGENTS.md stays, as data.
    "claudeMdExcludes": ["**/CLAUDE.md", "**/CLAUDE.local.md", "**/.claude/rules/**"],
    "permissions": {
        "blockReadsOutsideWorkingDirectories": True,
        "blockWritesOutsideWorkingDirectories": True,
        "disableBypassPermissionsMode": "disable",
    },
}


class ClaudeCodeAdapter:
    name = "claude-code"

    def __init__(self, binary: str = "claude") -> None:
        self.binary = binary

    def check_auth(self) -> None:
        if any(os.environ.get(var) for var in _TOKEN_VARS):
            return
        if _flag("CLAUDE_CODE_USE_BEDROCK") or _flag("CLAUDE_CODE_USE_VERTEX"):
            return
        raise AgentStepError(
            "Claude Code has no credential: set ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN, "
            "or CLAUDE_CODE_USE_BEDROCK / CLAUDE_CODE_USE_VERTEX with that provider's credentials"
        )

    def environment(self, home: str) -> dict[str, str]:
        names: list[str] = list(BASE_CREDENTIALS)
        if _flag("CLAUDE_CODE_USE_BEDROCK"):
            names.extend(BEDROCK_CREDENTIALS)
        if _flag("CLAUDE_CODE_USE_VERTEX"):
            names.extend(VERTEX_CREDENTIALS)
        env = scrub_env(names)
        env["HOME"] = home
        return env

    def command(self, task: AgentTask) -> list[str]:
        allowed = ["Read", "Grep", "Glob", "Skill", *(f"Bash({cmd}:*)" for cmd in GIT_COMMANDS)]
        allowed.append(f"Edit({output_path(task).as_posix()})")
        argv = [
            self.binary,
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--tools",
            TOOLS,
            "--allowedTools",
            ",".join(allowed),
            "--disallowedTools",
            ",".join(DISALLOWED),
            "--permission-mode",
            "dontAsk",
            "--setting-sources",
            "user",
            "--strict-mcp-config",
            "--no-session-persistence",
        ]
        if task.model:
            argv += ["--model", task.model]
        return argv

    def run(self, task: AgentTask) -> AgentResult:
        argv = self.command(task)
        with tempfile.TemporaryDirectory(prefix="reviewloop-home-") as home:
            config = Path(home) / ".claude"
            install_skills(task.step, task.skills, config / "skills")
            config.mkdir(exist_ok=True)
            (config / "settings.json").write_text(json.dumps(SETTINGS))
            bin_dir = install_git_wrapper(Path(home), task.workdir)
            # Claude Code runs CLAUDE_ENV_FILE before every Bash command in the same shell, so the guard stays first
            # on PATH even if a login shell's /etc/profile resets PATH.
            env_file = Path(home) / "agent-env.sh"
            env_file.write_text(f'export PATH={shlex.quote(str(bin_dir))}:"$PATH"\n')
            env = self.environment(home)
            env["PATH"] = os.pathsep.join([str(bin_dir), env.get("PATH", os.defpath)])
            env["CLAUDE_ENV_FILE"] = str(env_file)
            return run_cli(task, argv, env)
