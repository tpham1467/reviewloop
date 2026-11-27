import os
from pathlib import Path

from reviewloop.pipeline.context import RunContext
from reviewloop.stacks import detect_stacks, is_manifest

MAX_DEPTH = 3
MAX_MANIFEST_BYTES = 256_000
_SKIP_DIRS = frozenset({".git", "node_modules", "vendor", ".reviewloop"})


def _manifests(root: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    for here, dirs, files in os.walk(root):
        rel_dir = Path(here).relative_to(root)
        depth = len(rel_dir.parts)
        dirs[:] = [] if depth >= MAX_DEPTH else [d for d in dirs if d not in _SKIP_DIRS]
        for name in files:
            rel = (rel_dir / name).as_posix()
            path = Path(here) / name
            if is_manifest(rel) and path.stat().st_size <= MAX_MANIFEST_BYTES:
                found[rel] = path.read_text(encoding="utf-8", errors="replace")
    return found


class DetectStack:
    """Pick rule stacks from the changed paths and the manifests in the checkout (read from the sandbox copy)."""

    id = "detect-stack"
    group: str | None = None

    def run(self, ctx: RunContext) -> None:
        ctx.stacks = detect_stacks([d.path for d in ctx.reviewable()], _manifests(ctx.sandbox))
