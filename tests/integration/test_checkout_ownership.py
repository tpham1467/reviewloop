"""The CI checkout owned by another user (a container running as a different uid, ruling R83b).

Changing a file's owner needs root, so a small LD_PRELOAD shim makes `stat` report a foreign owner for paths under
the checkout; git's safe.directory check then fails exactly as it does in the container.

Whether a clone is refused at all depends on git: since 2.48 upload-pack no longer checks ownership ("cloning a
suspicious repository is a safe first step"), while older releases and distribution backports (Ubuntu 2.34, the
image's git) refuse it. The `ownership_enforced` fixture measures this once, so the foreign-owner tests run only
where the check fires; a passing positive test proves nothing when it never does.
"""

import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

from reviewloop.agents import isolation
from reviewloop.agents.isolation import prepare_sandbox, trusted_upload_pack

SHIM = r"""
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
static int foreign(const char *p) {
    const char *u = getenv("FAKE_FOREIGN_UNDER");
    return u && p && strncmp(p, u, strlen(u)) == 0;
}
#define WRAP(name, type) int name(const char *path, type *st) { \
    static int (*real)(const char *, type *); if (!real) real = dlsym(RTLD_NEXT, #name); \
    int r = real(path, st); if (r == 0 && foreign(path)) st->st_uid = 4242; return r; }
WRAP(stat, struct stat)
WRAP(lstat, struct stat)
WRAP(stat64, struct stat64)
WRAP(lstat64, struct stat64)
"""
GIT_IDENTITY = {"GIT_AUTHOR_NAME": "Dev", "GIT_AUTHOR_EMAIL": "dev@example.test", "GIT_COMMITTER_NAME": "Dev",
                "GIT_COMMITTER_EMAIL": "dev@example.test"}  # fmt: skip


def _git(cwd: Path, *args: str) -> str:
    env = {**os.environ, **isolation.GIT_ENV, **GIT_IDENTITY}
    return subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout  # noqa: S603, S607


def _foreign_env(shim: Path, repo: Path) -> dict[str, str]:
    return {**os.environ, **isolation.GIT_ENV, "LD_PRELOAD": str(shim), "FAKE_FOREIGN_UNDER": str(repo.resolve())}


@pytest.fixture(scope="module")
def ownership_enforced(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The shim, once this git is shown to refuse a plain clone of a foreign-owned repository; else skip."""
    if shutil.which("gcc") is None:
        pytest.skip("gcc is needed to build the ownership shim")
    root = tmp_path_factory.mktemp("ownership")
    (root / "shim.c").write_text(SHIM)
    shim = root / "shim.so"
    subprocess.run(["gcc", "-shared", "-fPIC", "-o", str(shim), str(root / "shim.c"), "-ldl"], check=True)  # noqa: S603, S607
    repo = root / "probe"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "probe")
    env = _foreign_env(shim, repo)
    status = subprocess.run(["git", "status"], cwd=repo, env=env, capture_output=True)  # noqa: S607
    if b"dubious ownership" not in status.stderr:
        pytest.skip("the shim cannot fake file ownership on this system (for example a static git)")
    clone = ["git", "clone", "-q", "--no-local", "--no-checkout", "--upload-pack=git upload-pack", str(repo), "out"]
    refused = subprocess.run(clone, cwd=root, env=env, capture_output=True)  # noqa: S603
    if b"dubious ownership" not in refused.stderr:
        version = _git(root, "--version").strip()
        pytest.skip(f"{version}: upload-pack does not check ownership, so no clone is refused to test against")
    return shim


@pytest.fixture
def foreign_checkout(ownership_enforced: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str]:
    """A checkout whose files git sees as owned by another uid; the PR head is reachable from no ref."""
    repo = tmp_path / "checkout dir $(touch pwned)"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "app.py").write_text("base\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "base")
    (repo / "app.py").write_text("head\n")
    _git(repo, "commit", "-q", "-am", "head")
    head = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "checkout", "-q", "--detach", "HEAD~1")
    _git(repo, "branch", "-D", "main")

    monkeypatch.setenv("LD_PRELOAD", str(ownership_enforced))
    monkeypatch.setenv("FAKE_FOREIGN_UNDER", str(repo.resolve()))
    return repo, head


def test_sandbox_from_foreign_owned_checkout(foreign_checkout: tuple[Path, str], tmp_path: Path) -> None:
    repo, head = foreign_checkout

    sandbox = prepare_sandbox(repo, tmp_path / "work" / "sandbox", head)

    # The head is reachable from no ref, so this also covers the fetch by id from the foreign-owned checkout.
    assert (sandbox / "app.py").read_text() == "head\n"
    assert not (tmp_path / "pwned").exists() and not Path("pwned").exists()


def test_foreign_owned_checkout_needs_upload_pack_trust(
    foreign_checkout: tuple[Path, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without the --upload-pack value, the env-only trust is not enough (the failure Task 16 hit)."""
    repo, head = foreign_checkout
    monkeypatch.setattr(isolation, "trusted_upload_pack", lambda source: "git upload-pack")

    with pytest.raises(isolation.AgentStepError, match="dubious ownership"):
        prepare_sandbox(repo, tmp_path / "work" / "sandbox", head)


def test_upload_pack_trusts_exactly_the_checkout(tmp_path: Path) -> None:
    source = tmp_path / "a b" / ".." / "co $(rm -rf ~); `x` 'q'"
    value = trusted_upload_pack(source)
    assert shlex.split(value) == ["git", "-c", f"safe.directory={source.resolve()}/.git", "upload-pack"]
    assert "*" not in value


def test_clone_and_fetch_pass_upload_pack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "checkout"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "app.py").write_text("base\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "head")
    head = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "checkout", "-q", "--detach", "HEAD~1")
    _git(repo, "branch", "-D", "main")
    calls: list[tuple[list[str], dict[str, str]]] = []
    real = subprocess.run

    def spy(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        calls.append((cmd, kw["env"]))  # type: ignore[arg-type]
        return real(cmd, **kw)  # type: ignore[call-overload]

    monkeypatch.setattr(isolation.subprocess, "run", spy)
    sandbox = tmp_path / "work" / "sandbox"
    prepare_sandbox(repo, sandbox, head)

    expected = f"--upload-pack={trusted_upload_pack(repo)}"
    by_verb = {cmd[1]: (cmd, env) for cmd, env in calls if cmd[1] in ("clone", "fetch")}
    for verb in ("clone", "fetch"):
        cmd, env = by_verb[verb]
        assert expected in cmd
        # The trust rides only on upload-pack, the one process that opens the checkout as a repository.
        assert "GIT_CONFIG_VALUE_0" not in env
    assert _git(sandbox, "rev-parse", "HEAD").strip() == head
