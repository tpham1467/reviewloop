import os
import subprocess
from pathlib import Path

import pytest

from reviewloop.agents import git_guard
from reviewloop.agents.git_guard import GitDenied, git_command, git_wrapper_main
from reviewloop.agents.isolation import install_git_wrapper

FAKE_TOKEN = "ghp_FAKE_WRAPPER_0000"  # noqa: S105


def _git(cwd: Path, *args: str) -> None:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True)  # noqa: S603, S607


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "sandbox"
    (root / "src").mkdir(parents=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "dev@example.test")
    _git(root, "config", "user.name", "Dev")
    (root / "a.py").write_text("".join(f"line {n}\n" for n in range(1, 31)))
    (root / "src" / "a.py").write_text("x = 1\n")
    (root / "sub").mkdir()
    (root / "sub" / "f.txt").write_text("sub file\n")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "first")
    (root / "a.py").write_text("".join(f"line {n}\n" for n in range(1, 32)))
    (root / "src" / "a.py").write_text("x = 2\n")
    _git(root, "commit", "-q", "-am", "second")
    secret = tmp_path / "runner" / ".git" / "config"
    secret.parent.mkdir(parents=True)
    secret.write_text(f"[http]\n\textraheader = AUTHORIZATION: basic {FAKE_TOKEN}\n")
    # A file whose name has a colon, next to the sandbox: `out:a.py` looks like `<rev>:<path>`.
    (tmp_path / "out:a.py").write_text(f"token {FAKE_TOKEN}\n")
    # sysfs-like tree: a directory name with colons and a link that `..` then climbs out of.
    device = tmp_path / "devices" / "pci0000:00" / "0000:00:00.0"
    device.mkdir(parents=True)
    (tmp_path / "bus" / "pci").mkdir(parents=True)
    (device / "subsystem").symlink_to(tmp_path / "bus" / "pci")
    return root


@pytest.fixture
def wrapper(repo: Path, tmp_path: Path) -> Path:
    return install_git_wrapper(tmp_path / "home", repo) / "git"


def _run(wrapper: Path, repo: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    base = {"PATH": os.environ["PATH"], "HOME": str(wrapper.parent.parent)}
    return subprocess.run(  # noqa: S603
        [str(wrapper), *args], cwd=repo, env={**base, **(env or {})}, capture_output=True, text=True, check=False
    )


OUTSIDE = "../runner/.git/config"
COLON_FILE = "../out:a.py"
# Lexically, the part after the first colon stays in the sandbox; on disk the link and `..` lead to the secret.
SYSFS = "../devices/pci0000:00/0000:00:00.0/subsystem/../../runner/.git/config"
DIFF_DASHDASH = "git diff paths must follow --: a.py"
REJECTED = [
    (["diff", "<secret>", "a.py"], "git path outside the sandbox: <secret>"),
    (["diff", OUTSIDE, "a.py"], f"git path outside the sandbox: {OUTSIDE}"),
    (["diff", "~/x", "a.py"], "git path outside the sandbox: ~/x"),
    (["diff", "<tmp>/out:a.py", "a.py"], "git path outside the sandbox: <tmp>/out:a.py"),
    (["diff", COLON_FILE, "a.py"], f"git path outside the sandbox: {COLON_FILE}"),
    (["diff", SYSFS, "a.py"], f"git path outside the sandbox: {SYSFS}"),
    (["show", SYSFS], f"git path outside the sandbox: {SYSFS}"),
    (["log", COLON_FILE], f"git path outside the sandbox: {COLON_FILE}"),
    (["diff", "a.py", "<secret>"], DIFF_DASHDASH),
    (["diff", "a.py", "<tmp>/out:a.py"], DIFF_DASHDASH),
    (["diff", "a.py", COLON_FILE], DIFF_DASHDASH),
    (["diff", "a.py", SYSFS], DIFF_DASHDASH),
    (["diff", "a.py"], DIFF_DASHDASH),
    (["diff", "HEAD", "src"], "git diff paths must follow --: src"),
    (["blame", "--con", OUTSIDE, "a.py"], "git option not allowed: --con"),
    (["blame", "--cont", OUTSIDE, "a.py"], "git option not allowed: --cont"),
    (["blame", f"--contents={OUTSIDE}", "a.py"], "git option not allowed: --contents"),
    (["blame", "-wS", OUTSIDE, "a.py"], "git option not allowed: -S"),
    (["blame", "-S", OUTSIDE, "a.py"], "git option not allowed: -S"),
    (["blame", "-Mx", "a.py"], "git option value not allowed: -Mx"),
    (["log", "--output=out.txt"], "git option not allowed: --output"),
    (["log", "--outp=out.txt"], "git option not allowed: --outp"),
    (["log", "--oneline=x"], "git option takes no value: --oneline=x"),
    (["log", "--format"], "git option needs =<value>: --format"),
    (["log", "-n"], "git option needs a value: -n"),
    (["log", "-nx"], "git option value not allowed: -nx"),
    (["log", "-"], "git option not allowed: -"),
    (["log", f"-L1,2:{OUTSIDE}"], f"git path outside the sandbox: {OUTSIDE}"),
    (["log", "-L1,2"], "git option value not allowed: -L1,2"),
    (["show", f"HEAD:{OUTSIDE}"], f"git path outside the sandbox: {OUTSIDE}"),
    (["log", "--", "<secret>"], "git path outside the sandbox: <secret>"),
    (["-c", "core.pager=sh", "log"], "git option not allowed: -c"),
    (["--git-dir=/x", "log"], "git option not allowed: --git-dir=/x"),
    (["diff", "--ext-diff"], "git option not allowed: --ext-diff"),
    (["log", "--textconv"], "git option not allowed: --textconv"),
    (["show", "HEAD:../../x"], "git path outside the sandbox: ../../x"),
    (["show", ":0:../../x"], "git path outside the sandbox: ../../x"),
    (["diff", "/dev/null", "a.py"], "git path outside the sandbox: /dev/null"),
    (["log", "-L:fn:../x"], "git path outside the sandbox: ../x"),
    (["diff", "~/x..HEAD"], "git path outside the sandbox: ~/x..HEAD"),
    (["status"], "git subcommand not allowed: status"),
    ([], "git subcommand not allowed: (none)"),
]
ALLOWED = [
    ["log", "-n5", "--oneline", "--", "src/a.py"],
    ["blame", "-L10,20", "a.py"],
    ["show", "HEAD:a.py"],
    ["diff", "HEAD~1..HEAD", "--", "a.py"],
    ["blame", "-wM20", "-C", "--date=short", "a.py"],
    ["log", "-L1,2:a.py", "-n", "1", "--format=%H/%s"],
    ["show", "-U3", "--stat", "HEAD"],
    ["diff", "--cached", "-U1", "--word-diff=plain"],
    ["show", "HEAD^{tree}"],
    ["show", "HEAD:./a.py"],
    ["log", "--grep=../../x", "-n1"],
    ["blame", "-L/line 3/,+2", "a.py"],
    ["log", "-L/line 1/,/line 2/:a.py", "-n1"],
    ["diff", "-U3", "--", "src"],
    ["show", "HEAD:sub/f.txt"],
    ["diff", "--", "a.py"],
    ["diff", "HEAD~1", "HEAD", "--", "sub/f.txt"],
]


def _fill(args: list[str], secret: Path) -> list[str]:
    tmp = secret.parent.parent.parent
    return [arg.replace("<secret>", str(secret)).replace("<tmp>", str(tmp)) for arg in args]


@pytest.mark.parametrize(("args", "message"), REJECTED)
def test_wrapper_rejects(wrapper: Path, repo: Path, tmp_path: Path, args: list[str], message: str) -> None:
    secret = tmp_path / "runner" / ".git" / "config"

    result = _run(wrapper, repo, *_fill(args, secret))

    assert result.returncode == 2
    assert result.stderr.strip() == "reviewloop: " + _fill([message], secret)[0]
    assert FAKE_TOKEN not in result.stdout
    assert not (repo / "out.txt").exists()


@pytest.mark.parametrize("args", ALLOWED)
def test_wrapper_allows(wrapper: Path, repo: Path, args: list[str]) -> None:
    result = _run(wrapper, repo, *args)

    assert result.returncode == 0, result.stderr


def test_wrapper_output_is_real_git(wrapper: Path, repo: Path) -> None:
    assert _run(wrapper, repo, "show", "HEAD:src/a.py").stdout == "x = 2\n"


def test_wrapper_rejects_cwd_outside_sandbox(wrapper: Path, tmp_path: Path) -> None:
    result = _run(wrapper, tmp_path / "runner", "log")

    assert result.returncode == 2
    assert "git must run inside the sandbox" in result.stderr


def test_wrapper_ignores_external_diff_and_git_env(wrapper: Path, repo: Path, tmp_path: Path) -> None:
    marker = tmp_path / "ran"
    tool = tmp_path / "tool.sh"
    tool.write_text(f"#!/bin/sh\ntouch {marker}\n")
    tool.chmod(0o755)
    _git(repo, "config", "diff.external", str(tool))
    env = {
        "GIT_EXTERNAL_DIFF": str(tool),
        "GIT_DIR": str(tmp_path / "runner" / ".git"),
        "GIT_WORK_TREE": str(tmp_path),
        "GIT_CONFIG_PARAMETERS": f"'core.pager'='{tool}'",
    }

    for args in (["diff", "HEAD~1..HEAD"], ["log", "-p", "-n1"], ["show", "HEAD"]):
        result = _run(wrapper, repo, *args, env=env)
        assert result.returncode == 0, result.stderr
        assert "x = 2" in result.stdout

    assert not marker.exists()


def test_wrapper_script_is_isolated_python(wrapper: Path) -> None:
    import sys

    first = wrapper.read_text().splitlines()[0]
    assert first == f"#!{sys.executable} -I"
    assert os.access(wrapper, os.X_OK)


@pytest.mark.parametrize(("args", "message"), REJECTED)
def test_git_command_rejects(repo: Path, tmp_path: Path, args: list[str], message: str) -> None:
    secret = tmp_path / "runner" / ".git" / "config"

    with pytest.raises(GitDenied) as info:
        git_command(_fill(args, secret), real_git="/usr/bin/git", root=str(repo), cwd=str(repo))

    assert str(info.value) == _fill([message], secret)[0]


@pytest.mark.parametrize("args", ALLOWED)
def test_git_command_adds_safe_flags(repo: Path, args: list[str]) -> None:
    argv = git_command(args, real_git="/usr/bin/git", root=str(repo), cwd=str(repo))

    extra = ["--no-textconv"] if args[0] == "blame" else ["--no-ext-diff", "--no-textconv"]
    assert argv == ["/usr/bin/git", "--no-pager", "--literal-pathspecs", args[0], *extra, *args[1:]]


def test_wrapper_disables_pathspec_magic(wrapper: Path, repo: Path) -> None:
    result = _run(wrapper, repo, "log", "--oneline", "--", ":(top)*")

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_wrapper_main_execs_with_clean_env(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, list[str], dict[str, str]]] = []
    monkeypatch.chdir(repo)
    monkeypatch.setenv("GIT_DIR", "/elsewhere")
    monkeypatch.setenv("GIT_EXTERNAL_DIFF", "tool")
    monkeypatch.setattr(git_guard.os, "execve", lambda path, argv, env: calls.append((path, argv, env)))

    git_wrapper_main(["log", "-n1"], real_git="/usr/bin/git", root=str(repo))

    path, argv, env = calls[0]
    assert path == "/usr/bin/git"
    assert argv[:4] == ["/usr/bin/git", "--no-pager", "--literal-pathspecs", "log"]
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert env["GIT_CONFIG_GLOBAL"] == os.devnull
    assert env["GIT_PAGER"] == "cat"
    assert "GIT_DIR" not in env
    assert "GIT_EXTERNAL_DIFF" not in env


def test_wrapper_main_reports_denial(repo: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.chdir(repo)

    assert git_wrapper_main(["status"], real_git="/usr/bin/git", root=str(repo)) == 2

    assert capsys.readouterr().err == "reviewloop: git subcommand not allowed: status\n"
