import os
import subprocess
from pathlib import Path

import pytest

from reviewloop.agents import isolation
from reviewloop.agents.isolation import prepare_sandbox, scrub_env, snapshot, verify_only_output_changed
from reviewloop.errors import AgentStepError

OUTPUT = Path(".reviewloop/out/review.json")
TRANSCRIPT = Path(".reviewloop/logs/review-1.jsonl")


def _git(cwd: Path, *args: str) -> str:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    return subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def test_sandbox_has_no_remote_or_credentials(source_repo: Path, tmp_path: Path) -> None:
    _git(source_repo, "remote", "add", "origin", "https://x-access-token:ghp_FAKE0000@github.com/o/r.git")
    _git(source_repo, "config", "http.https://github.com/.extraheader", "AUTHORIZATION: basic ghp_FAKE0000")
    _git(source_repo, "config", "credential.helper", "store")
    (source_repo / ".git" / "hooks" / "post-checkout").write_text("#!/bin/sh\necho pwned\n")

    sandbox = prepare_sandbox(source_repo, tmp_path / "sandbox")

    config = _git(sandbox, "config", "--list")
    assert "ghp_FAKE" not in config
    assert "extraheader" not in config
    assert "credential" not in config
    assert _git(sandbox, "remote").strip() == ""
    assert list((sandbox / ".git" / "hooks").iterdir()) == []


def test_sandbox_matches_source_head_with_history(source_repo: Path, tmp_path: Path) -> None:
    head = _git(source_repo, "rev-parse", "HEAD").strip()

    sandbox = prepare_sandbox(source_repo, tmp_path / "sandbox")

    assert _git(sandbox, "rev-parse", "HEAD").strip() == head
    assert len(_git(sandbox, "log", "--oneline").splitlines()) == 2
    assert (sandbox / "app.py").read_text() == "print('hello')\n"
    assert (sandbox / ".reviewloop" / "out").is_dir()


def test_sandbox_follows_detached_source_head(source_repo: Path, tmp_path: Path) -> None:
    _git(source_repo, "checkout", "-q", "--detach", "HEAD~1")
    head = _git(source_repo, "rev-parse", "HEAD").strip()

    sandbox = prepare_sandbox(source_repo, tmp_path / "sandbox")

    assert _git(sandbox, "rev-parse", "HEAD").strip() == head


def test_sandbox_strips_rewrite_and_include_config(source_repo: Path, tmp_path: Path) -> None:
    sandbox = prepare_sandbox(source_repo, tmp_path / "sandbox")
    # A clone never copies the source config, so plant the keys in the sandbox and strip again.
    _git(sandbox, "config", "url.https://ghp_FAKE0000@github.com/.insteadOf", "https://github.com/")
    _git(sandbox, "config", "includeIf.gitdir:/.path", "evil.cfg")
    _git(sandbox, "config", "credential.https://github.com.username", "bot")
    _git(sandbox, "config", "core.hooksPath", "evil-hooks")
    _git(sandbox, "config", "core.fsmonitor", "evil-command")

    isolation._strip_config(sandbox)  # pyright: ignore[reportPrivateUsage]

    config = _git(sandbox, "config", "--list").lower()
    assert "insteadof" not in config
    assert "includeif" not in config
    assert "credential" not in config
    assert "hookspath" not in config
    assert "fsmonitor" not in config


def _commit(repo: Path, *paths: str) -> None:
    _git(repo, "add", "--", *paths)
    _git(repo, "commit", "-q", "-m", "add " + " ".join(paths))


def test_committed_symlinks_become_plain_files(source_repo: Path, tmp_path: Path) -> None:
    secret = tmp_path / "runner" / ".git" / "config"
    secret.parent.mkdir(parents=True)
    secret.write_text("[http]\n\textraheader = AUTHORIZATION: basic ghp_FAKE0000\n")
    (source_repo / "cfg").symlink_to(secret)
    (source_repo / "p").symlink_to("/proc")
    _commit(source_repo, "cfg", "p")

    sandbox = prepare_sandbox(source_repo, tmp_path / "sandbox")

    for name, target in (("cfg", str(secret)), ("p", "/proc")):
        path = sandbox / name
        assert not path.is_symlink()
        assert path.is_file()
        assert path.read_text() == target
    assert _git(sandbox, "config", "core.symlinks").strip() == "false"


def test_clean_tree_rejects_leftover_symlink(tmp_path: Path) -> None:
    (tmp_path / "tree" / ".git").mkdir(parents=True)
    (tmp_path / "tree" / "sub").mkdir()
    (tmp_path / "tree" / "sub" / "link").symlink_to("/proc")

    with pytest.raises(AgentStepError, match=r"symlink.*sub/link"):
        isolation._clean_tree(tmp_path / "tree")  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize("kind", ["link", "dir"])
def test_committed_reviewloop_path_is_replaced(source_repo: Path, tmp_path: Path, kind: str) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    if kind == "link":
        (source_repo / ".reviewloop").symlink_to(outside)
    else:
        (source_repo / ".reviewloop" / "out").mkdir(parents=True)
        (source_repo / ".reviewloop" / "out" / "review.json").write_text('{"message": "planted", "count": 1}')
    _commit(source_repo, ".reviewloop")

    sandbox = prepare_sandbox(source_repo, tmp_path / "sandbox")

    root = sandbox / ".reviewloop"
    assert root.is_dir()
    assert not root.is_symlink()
    assert sorted(p.relative_to(root).as_posix() for p in root.rglob("*")) == ["out"]
    assert list(outside.iterdir()) == []


def test_committed_agent_config_is_removed(source_repo: Path, tmp_path: Path) -> None:
    files = {
        ".claude/settings.json": '{"permissions": {"allow": ["Bash"]}}',
        ".claude/skills/evil/SKILL.md": "---\nname: evil\n---\n",
        ".claude/agents/a.md": "agent",
        "sub/.claude/commands/c.md": "command",
        ".mcp.json": "{}",
        "sub/.mcp.json": "{}",
        "AGENTS.md": "project notes",
        "CLAUDE.md": "project notes",
    }
    for rel, text in files.items():
        (source_repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (source_repo / rel).write_text(text)
    _commit(source_repo, *files)

    sandbox = prepare_sandbox(source_repo, tmp_path / "sandbox")

    assert not (sandbox / ".claude").exists()
    assert not (sandbox / "sub" / ".claude").exists()
    assert not (sandbox / ".mcp.json").exists()
    assert not (sandbox / "sub" / ".mcp.json").exists()
    assert (sandbox / "AGENTS.md").read_text() == "project notes"
    assert (sandbox / "CLAUDE.md").exists()
    # Removed tracked files are marked skip-worktree, so the agent sees no fake deletions.
    assert _git(sandbox, "status", "--porcelain") == ""
    assert _git(sandbox, "diff", "--stat") == ""


def test_committed_reviewloop_files_do_not_show_as_deleted(source_repo: Path, tmp_path: Path) -> None:
    (source_repo / ".reviewloop").mkdir()
    (source_repo / ".reviewloop" / "notes.txt").write_text("planted")
    _commit(source_repo, ".reviewloop")

    sandbox = prepare_sandbox(source_repo, tmp_path / "sandbox")

    assert _git(sandbox, "diff", "--name-status") == ""


def test_sandbox_ignores_runner_global_git_config(
    source_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "hook-ran"
    hooks = tmp_path / "global-hooks"
    hooks.mkdir()
    (hooks / "post-checkout").write_text(f"#!/bin/sh\ntouch {marker}\n")
    (hooks / "post-checkout").chmod(0o755)
    config = tmp_path / "global.gitconfig"
    config.write_text(f"[core]\n\thooksPath = {hooks}\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))

    prepare_sandbox(source_repo, tmp_path / "sandbox")

    assert not marker.exists()


def test_sandbox_fails_clearly_for_non_repo(tmp_path: Path) -> None:
    with pytest.raises(AgentStepError, match="sandbox"):
        prepare_sandbox(tmp_path / "missing", tmp_path / "sandbox")


def test_sandbox_fails_clearly_for_dir_without_git(tmp_path: Path) -> None:
    (tmp_path / "plain").mkdir()

    with pytest.raises(AgentStepError, match="sandbox: git rev-parse failed"):
        prepare_sandbox(tmp_path / "plain", tmp_path / "sandbox")


def test_scrub_env_drops_vcs_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_FAKE0000")
    monkeypatch.setenv("REVIEWLOOP_GITLAB_TOKEN", "glpat-FAKE0000")
    monkeypatch.setenv("CI_JOB_TOKEN", "FAKE0000")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-FAKE0000")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "FAKE0000")
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("LANG", "C.UTF-8")

    env = scrub_env(["ANTHROPIC_API_KEY", "AWS_*", "CLAUDE_CODE_OAUTH_TOKEN"])

    assert "GITHUB_TOKEN" not in env
    assert "REVIEWLOOP_GITLAB_TOKEN" not in env
    assert "CI_JOB_TOKEN" not in env
    assert env["ANTHROPIC_API_KEY"] == "sk-ant-FAKE0000"
    assert env["AWS_REGION"] == "us-east-1"
    assert env["AWS_SECRET_ACCESS_KEY"] == "FAKE0000"  # noqa: S105
    assert env["PATH"] == "/usr/bin"
    assert env["LANG"] == "C.UTF-8"
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env
    assert set(env) <= {
        "PATH",
        "HOME",
        "LANG",
        "LC_ALL",
        "TMPDIR",
        "ANTHROPIC_API_KEY",
        "AWS_REGION",
        "AWS_SECRET_ACCESS_KEY",
    }


def _tree(tmp_path: Path) -> Path:
    root = tmp_path / "tree"
    (root / ".reviewloop" / "out").mkdir(parents=True)
    (root / "src").mkdir()
    (root / "src" / "a.py").write_text("a = 1\n")
    (root / ".git").mkdir()
    (root / ".git" / "index").write_text("v1")
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main")
    return root


def test_snapshot_covers_git_dir_except_index(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    (root / "link").symlink_to("src/a.py")

    snap = snapshot(root)

    assert set(snap) == {"src/a.py", "link", ".git/HEAD"}


def test_verify_allows_output_transcript_and_index(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    before = snapshot(root)
    (root / OUTPUT).write_text("{}")
    (root / TRANSCRIPT).parent.mkdir()
    (root / TRANSCRIPT).write_text("{}")
    (root / ".git" / "index").write_text("v2")

    verify_only_output_changed(before, snapshot(root), OUTPUT, transcript=TRANSCRIPT)


def test_verify_rejects_extra_write(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    before = snapshot(root)
    (root / OUTPUT).write_text("{}")
    (root / "src" / "a.py").write_text("a = 2\n")
    (root / "planted.sh").write_text("echo hi\n")
    (root / ".reviewloop" / "out" / "other.json").write_text("{}")

    with pytest.raises(AgentStepError) as info:
        verify_only_output_changed(before, snapshot(root), OUTPUT, transcript=TRANSCRIPT)

    listed = str(info.value).split(": ", 1)[1].split(", ")
    assert listed == [".reviewloop/out/other.json", "planted.sh", "src/a.py"]


def test_verify_rejects_other_log_file(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    before = snapshot(root)
    (root / ".reviewloop" / "logs").mkdir()
    (root / ".reviewloop" / "logs" / "review-2.jsonl").write_text("{}")

    with pytest.raises(AgentStepError, match=r"review-2\.jsonl"):
        verify_only_output_changed(before, snapshot(root), OUTPUT, transcript=TRANSCRIPT)


def test_verify_rejects_git_write(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    before = snapshot(root)
    (root / ".git" / "refs" / "heads").mkdir(parents=True)
    (root / ".git" / "refs" / "heads" / "evil").write_text("0" * 40)
    (root / ".git" / "HEAD").write_text("ref: refs/heads/evil")

    with pytest.raises(AgentStepError, match=r"\.git/HEAD, \.git/refs/heads/evil"):
        verify_only_output_changed(before, snapshot(root), OUTPUT)


def test_verify_rejects_mode_change(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    before = snapshot(root)
    (root / "src" / "a.py").chmod(0o755)

    with pytest.raises(AgentStepError, match=r"src/a\.py"):
        verify_only_output_changed(before, snapshot(root), OUTPUT)


def test_verify_rejects_deleted_file(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    before = snapshot(root)
    (root / "src" / "a.py").unlink()

    with pytest.raises(AgentStepError, match=r"src/a\.py"):
        verify_only_output_changed(before, snapshot(root), OUTPUT)
