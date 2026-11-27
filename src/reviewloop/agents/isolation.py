"""Hard isolation around an agent CLI (spec 8.2).

The agent reads untrusted PR code, so reviewloop does not rely on the CLI alone: the agent works in a credential-free,
link-free clone, gets a scrubbed environment, and every run is checked to have changed nothing but its one output
file (plus the transcript reviewloop writes and the git index that `git diff` refreshes).
"""

import hashlib
import os
import re
import shutil
import stat
import subprocess
import sys
from collections.abc import Iterable, Mapping
from fnmatch import fnmatchcase
from pathlib import Path

from reviewloop.agents import git_guard
from reviewloop.errors import AgentStepError, ReviewloopError

BASE_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")
GIT_INDEX = ".git/index"
SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"
# Agent configuration a PR can commit: project skills, settings, permissions, hooks, rules, subagents, commands and MCP
# servers of Claude Code (`.claude/`, `.mcp.json`), Codex (`.codex/`, `.agents/skills`) and Cursor (`.cursor/` with
# cli.json, permissions.json, sandbox.json, hooks.json, mcp.json, rules and skills; `.cursorrules`). `.cursorignore`
# would hide files from the review. All are removed from the copy so no CLI loads them; AGENTS.md and CLAUDE.md stay,
# they are data the review may read. An entry with a `/` (`.agents/skills`) is removed only at that path, under any
# directory; the rest of its parent stays.
AGENT_CONFIG_DIRS = (".claude", ".codex", ".cursor", ".agents/skills")
AGENT_CONFIG_FILES = (".mcp.json", ".cursorrules", ".cursorignore", ".cursorindexingignore")
# The runner's own git config could add hooks, an fsmonitor command or credentials, so none of it is read.
GIT_ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0"}
# A full commit id; git arguments built from VCS data must be one before they reach git.
COMMIT_SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
HEAD_FETCH_HINT = (
    "fetch the pull request head into the CI checkout before reviewloop runs "
    "(GitHub: git fetch origin refs/pull/<n>/head; GitLab: git fetch origin refs/merge-requests/<iid>/head)"
)

# CI checkout config that can carry a credential or run a command: an `http.<url>.extraHeader` (actions/checkout), a
# credential helper, a URL rewrite, an included file, an askpass or ssh command. reviewloop never fetches, so all go.
_CHECKOUT_CREDENTIAL_CONFIG = re.compile(
    r"^(credential\..*|http\.(.*\.)?extraheader|url\..*\.(push)?insteadof|include\..*|includeif\..*"
    r"|core\.(askpass|sshcommand))$"
)
# Values that are URLs, where only the userinfo is a credential: the host and path stay.
_URL_VALUE = re.compile(r"^(remote\..+\.(url|pushurl|proxy)|http\.(.+\.)?proxy)$")

# Config that can carry a credential, load another config file or run a command. A fresh clone does not copy the
# source's config, but a `config` file in a git template dir does land in the clone, so strip it.
_UNSAFE_CONFIG = re.compile(
    r"^(credential\..*|http\.(.*\.)?extraheader|url\..*\.(push)?insteadof|include\..*|includeif\..*"
    r"|core\.(hookspath|fsmonitor|sshcommand|askpass)|filter\..*|diff\..*\.(textconv|command))$"
)


def _real_git() -> str:
    git = shutil.which("git")
    if git is None:
        raise AgentStepError("sandbox: git is not installed")
    return os.path.realpath(git)


def _git(cwd: Path, *args: str, stdin: str | None = None) -> str:
    env = {**os.environ, **GIT_ENV}
    result = subprocess.run(  # noqa: S603
        [_real_git(), *args], cwd=cwd, env=env, input=stdin, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise AgentStepError(f"sandbox: git {args[0]} failed: {result.stderr.strip()}")
    return result.stdout


def _strip_config(repo: Path) -> None:
    for remote in _git(repo, "remote").split():
        _git(repo, "remote", "remove", remote)
    listing = _git(repo, "config", "--local", "--no-includes", "--name-only", "--list")
    for key in sorted(set(listing.split())):
        if _UNSAFE_CONFIG.match(key.lower()):
            _git(repo, "config", "--local", "--unset-all", key)


def _without_userinfo(url: str) -> str:
    """`url` without `user[:password]@`, for URLs with a scheme and for bare `user:pass@host:port` proxy values."""
    scheme, sep, rest = url.partition("://")
    if not sep:
        scheme, rest = "", url
    authority, slash, path = rest.partition("/")
    if "@" not in authority:
        return url
    return f"{scheme}{sep}{authority.rsplit('@', 1)[1]}{slash}{path}"


def _strip_credentials(repo: Path, where: list[str]) -> None:
    """Apply the checkout credential rules to one config file, `where` being `--local` or `--file <path>`."""
    listing = _git(repo, "config", *where, "--no-includes", "--name-only", "--list")
    removed: set[str] = set()
    for key in sorted(set(listing.split())):
        section, _, rest = key.partition(".")
        subsection = rest.rpartition(".")[0]
        if section.lower() in ("url", "http") and subsection and _without_userinfo(subsection) != subsection:
            # The credential is in the section name (`[url "https://x:tok@host/"]`), which outlives its keys.
            name = f"{section}.{subsection}"
            if name not in removed:
                removed.add(name)
                _git(repo, "config", *where, "--remove-section", name)
        elif _URL_VALUE.match(key.lower()):
            values = _git(repo, "config", *where, "--no-includes", "--get-all", key).splitlines()
            _git(repo, "config", *where, "--unset-all", key)
            for value in values:
                _git(repo, "config", *where, "--add", key, _without_userinfo(value))
        elif _CHECKOUT_CREDENTIAL_CONFIG.match(key.lower()):
            _git(repo, "config", *where, "--unset-all", key)


def strip_checkout_credentials(source: Path) -> None:
    """Remove git credentials from the CI job's own checkout `source` (ruling R31). The init step calls it before the
    first agent run.

    GitLab Runner keeps `gitlab-ci-token:<CI_JOB_TOKEN>@` in the `origin` URL and actions/checkout persists an
    `http.<url>.extraheader`; an agent able to read an absolute path could read either. reviewloop talks to the VCS
    through API tokens and never fetches or pushes after checkout, so the job's ephemeral checkout does not need them.
    URL values (remote URLs, proxies) keep their host and path; only the userinfo goes. Submodule gitdirs under
    `.git/modules/` get the same treatment.
    """
    _strip_credentials(source, ["--local"])
    modules = source / ".git" / "modules"
    for root, _dirs, files in os.walk(modules):
        # A submodule gitdir has its own HEAD; a `config` file elsewhere (a ref named `config`) is not a git config.
        if "config" in files and "HEAD" in files:
            _strip_credentials(source, ["--file", str(Path(root) / "config")])


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def _clean_tree(dest: Path) -> None:
    """Remove committed agent config and `.reviewloop`, and fail if any symlink is left in the working tree."""
    links: list[str] = []
    removed: list[str] = []
    for root, dirs, files in os.walk(dest):
        here = Path(root)
        if here == dest:
            dirs[:] = [d for d in dirs if d != ".git"]
        for name in [*dirs, *files]:
            path = here / name
            if path.is_symlink():
                links.append(path.relative_to(dest).as_posix())
            elif name in AGENT_CONFIG_DIRS or name in AGENT_CONFIG_FILES:
                removed.append(path.relative_to(dest).as_posix())
                _remove(path)
        for nested in (c for c in AGENT_CONFIG_DIRS if "/" in c):
            path = here / nested
            if path.is_dir() and not path.is_symlink() and not (here / nested.split("/")[0]).is_symlink():
                removed.append(path.relative_to(dest).as_posix())
                _remove(path)
        dirs[:] = [d for d in dirs if not (here / d).is_symlink() and d not in AGENT_CONFIG_DIRS]
    if links:
        raise AgentStepError(f"sandbox: symlink left in the copy: {', '.join(sorted(links))}")
    # A committed `.reviewloop` (file, directory, or link turned plain file) would hold planted inputs or outputs.
    if (dest / ".reviewloop").exists() or (dest / ".reviewloop").is_symlink():
        removed.append(".reviewloop")
        _remove(dest / ".reviewloop")
    if removed:
        # Mark the removed tracked files skip-worktree so `git diff` and `git status` in the copy do not show them
        # as deleted: the agent would otherwise review deletions the PR never made.
        tracked = _git(dest, "--literal-pathspecs", "ls-files", "-z", "--", *removed)
        if tracked:
            _git(dest, "update-index", "--skip-worktree", "-z", "--stdin", stdin=tracked)


def prepare_sandbox(source: Path, dest: Path, rev: str) -> Path:
    """Clone `source` at commit `rev` into `dest` with no remotes, credentials, includes, hooks, links or agent config.

    `rev` is the PR head, independent of what the CI checked out (ruling R80): the checkout stays the trusted base and
    only needs the head's objects, which may be reachable from no ref (a fetched `refs/pull/<n>/head`).

    Committed symlinks are checked out as plain files holding the link target, so no allowed command can read
    through one to a file outside the copy (the runner's `.git/config`, `/proc/self/environ`). Untracked inputs
    (the `.reviewloop/` files a step reads) are written by the caller after this returns.
    """
    if not source.is_dir():
        raise AgentStepError(f"sandbox: source checkout {source} does not exist")
    if not COMMIT_SHA.fullmatch(rev):
        raise ReviewloopError("sandbox: the pull request head is not a full hex commit id")
    _git(source, "rev-parse", "--git-dir")
    try:
        _git(source, "cat-file", "-e", f"{rev}^{{commit}}")
    except AgentStepError:
        raise ReviewloopError(f"sandbox: commit {rev} is not in the CI checkout; {HEAD_FETCH_HINT}") from None
    dest.parent.mkdir(parents=True, exist_ok=True)
    _git(dest.parent, "clone", "--no-local", "--no-hardlinks", "--no-checkout", "--quiet", str(source), str(dest))
    _strip_config(dest)
    _git(dest, "config", "--local", "core.symlinks", "false")
    # Remove hooks before the checkout so no template hook can run.
    for hook in (dest / ".git" / "hooks").iterdir():
        _remove(hook)
    try:
        _git(dest, "cat-file", "-e", f"{rev}^{{commit}}")
    except AgentStepError:
        # The clone copies refs only; a head that no ref reaches is fetched by id.
        _git(dest, "fetch", "--quiet", "--no-tags", str(source), rev)
    _git(dest, "-c", "advice.detachedHead=false", "checkout", "--quiet", "--detach", rev)
    _clean_tree(dest)
    (dest / ".reviewloop" / "out").mkdir(parents=True)
    return dest


def install_git_wrapper(home: Path, root: Path) -> Path:
    """Write the git allowlist guard as `<home>/bin/git` and return that `bin` directory.

    Every adapter puts the returned directory first on the agent's PATH, so `git` in the agent's shell is the guard.
    The real git is resolved now, with the runner's PATH, and baked into the script with the sandbox root.
    """
    bin_dir = home / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    source = Path(git_guard.__file__).read_text()
    entry = (
        "\n\nif __name__ == '__main__':\n"
        f"    sys.exit(git_wrapper_main(sys.argv[1:], real_git={_real_git()!r}, root={str(root.resolve())!r}))\n"
    )
    script = bin_dir / "git"
    script.write_text(f"#!{sys.executable} -I\n{source}{entry}")
    script.chmod(0o755)
    return bin_dir


def install_skills(step: str, skills: Iterable[str], dest: Path) -> None:
    """Copy each bundled skill `src/reviewloop/skills/<name>/` to `dest/<name>/` in the agent's throwaway HOME."""
    for skill in skills:
        source = SKILLS_DIR / skill
        if not (source / "SKILL.md").is_file():
            raise AgentStepError(f"step '{step}': unknown skill {skill!r}")
        shutil.copytree(source, dest / skill)


def scrub_env(allowed: Iterable[str]) -> dict[str, str]:
    """Environment for an agent: the base variables plus `allowed` names (shell-style `*` patterns accepted)."""
    patterns = [*BASE_ENV, *allowed]
    return {key: value for key, value in os.environ.items() if any(fnmatchcase(key, p) for p in patterns)}


def snapshot(dest: Path) -> dict[str, str]:
    """Map every path under `dest` (except `.git/index`) to its file type, mode and content sha256.

    Symlinks are hashed by their target text, never followed, so a link out of the sandbox is not read.
    """
    result: dict[str, str] = {}
    for root, dirs, files in os.walk(dest):
        here = Path(root)
        for name in [*files, *(d for d in dirs if (here / d).is_symlink())]:
            path = here / name
            rel = path.relative_to(dest).as_posix()
            if rel == GIT_INDEX:
                continue
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                digest = hashlib.sha256(os.readlink(path).encode()).hexdigest()
            else:
                with path.open("rb") as handle:
                    digest = hashlib.file_digest(handle, "sha256").hexdigest()
            result[rel] = f"{info.st_mode:o}:{digest}"
    return result


def verify_only_output_changed(
    before: Mapping[str, str], after: Mapping[str, str], output: Path, transcript: Path | None = None
) -> None:
    """Raise unless the only added, changed or removed path is `output` (relative to the sandbox).

    `transcript` is the log file reviewloop itself writes from the agent's stdout during the run.
    """
    allowed = {output.as_posix(), *([transcript.as_posix()] if transcript else [])}
    changed = sorted(
        path for path in before.keys() | after.keys() if before.get(path) != after.get(path) and path not in allowed
    )
    if changed:
        raise AgentStepError(f"agent changed files outside {output.as_posix()}: {', '.join(changed)}")
