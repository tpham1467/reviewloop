"""Host git in the CI checkout, run without the runner's git config (hooks, helpers, credentials) or a shell."""

import subprocess
from pathlib import Path

from reviewloop.agents.isolation import host_git_env


def run_git(cwd: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    """git in the CI checkout `cwd`, which is trusted as `safe.directory` (ruling R83); never call it on a sandbox."""
    return subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=cwd,
        env=host_git_env(cwd),
        capture_output=True,
        check=False,
    )
