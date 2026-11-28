"""Host git in the CI checkout, run without the runner's git config (hooks, helpers, credentials) or a shell."""

import re
import subprocess
from pathlib import Path

from reviewloop.agents.isolation import host_git_env

_SHA = re.compile(r"[0-9a-f]{7,64}")


def is_sha(value: str) -> bool:
    """A hex commit SHA, safe to pass to git as an argument (it cannot start with `-`)."""
    return _SHA.fullmatch(value) is not None


def run_git(cwd: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    """git in the CI checkout `cwd`, which is trusted as `safe.directory` (ruling R83); never call it on a sandbox."""
    return subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=cwd,
        env=host_git_env(cwd),
        capture_output=True,
        check=False,
    )
