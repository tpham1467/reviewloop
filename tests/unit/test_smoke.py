import json
import tomllib
from pathlib import Path

import reviewloop

ROOT = Path(__file__).resolve().parents[2]
LOCK_PATH = '$.package[?(@.name.value=="reviewloop-ai")].version'


def test_version_strings_agree() -> None:
    """release-please bumps all three on the release commit; a stale uv.lock fails `uv sync --locked` in the image."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    locked = next(p["version"] for p in lock["package"] if p["name"] == project["name"])
    assert reviewloop.__version__ == project["version"] == locked


def test_release_please_bumps_every_version_string() -> None:
    config = json.loads((ROOT / "release-please-config.json").read_text(encoding="utf-8"))
    extra = config["packages"]["."]["extra-files"]
    assert {"type": "toml", "path": "uv.lock", "jsonpath": LOCK_PATH} in extra
    assert {"type": "generic", "path": "src/reviewloop/__init__.py"} in extra
    assert "x-release-please-version" in (ROOT / "src/reviewloop/__init__.py").read_text(encoding="utf-8")


def test_every_pinned_image_version_is_bumped_by_release_please() -> None:
    config = json.loads((ROOT / "release-please-config.json").read_text(encoding="utf-8"))
    bumped = {f["path"] for f in config["packages"]["."]["extra-files"] if f["type"] == "generic"}
    for path in ("examples/bitbucket-pipelines.yml", "examples/Jenkinsfile", "src/reviewloop/cli_snippets.py"):
        assert path in bumped
        pinned = [ln for ln in (ROOT / path).read_text(encoding="utf-8").splitlines() if "ghcr.io/tpham1467" in ln]
        assert pinned and all("x-release-please-version" in ln and ":0." in ln for ln in pinned), path
