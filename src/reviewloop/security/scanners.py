"""Optional secret and static-analysis scanners.

Output is a hint for the security-review agent. Secret values are dropped at parse time and never logged.
Scanners are best effort: a missing binary, a non-zero exit, a timeout or unparsable output is logged and skipped.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from reviewloop.agents.isolation import host_git_env

log = logging.getLogger("reviewloop.security")

TIMEOUT_SECONDS = 90
# opengrep rules pinned to a commit and shipped in the image (ruling R87); never `--config auto`, which would download
# an unpinned registry pack on every run. Outside the image the env names a local rules directory.
RULES_ENV = "REVIEWLOOP_OPENGREP_RULES"
DEFAULT_RULES = Path("/opt/opengrep-rules")


@dataclass(frozen=True)
class ScanFinding:
    tool: str
    rule: str
    path: str
    line: int | None
    message: str
    in_history: bool


def _as_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _parse_gitleaks(report: Path) -> list[ScanFinding]:
    data: Any = json.loads(report.read_text() or "[]")
    # Only whitelisted fields are read: Secret and Match are never touched.
    return [
        ScanFinding(
            tool="gitleaks",
            rule=str(item.get("RuleID", "")),
            path=str(item.get("File", "")),
            line=_as_int(item.get("StartLine")),
            message=str(item.get("Description", "")),
            in_history=True,
        )
        for item in data
    ]


def _parse_sarif(report: Path) -> list[ScanFinding]:
    data: Any = json.loads(report.read_text())
    results: list[Any] = [r for run in data.get("runs", []) for r in run.get("results", [])]
    out: list[ScanFinding] = []
    for result in results:
        locations: list[Any] = result.get("locations") or []
        physical: Any = locations[0].get("physicalLocation", {}) if locations else {}
        out.append(
            ScanFinding(
                tool="opengrep",
                rule=str(result.get("ruleId", "")),
                path=str(physical.get("artifactLocation", {}).get("uri", "")),
                line=_as_int(physical.get("region", {}).get("startLine")),
                message=str(result.get("message", {}).get("text", "")),
                in_history=False,
            )
        )
    return out


def _run(
    cmd: list[str],
    workdir: Path,
    report: Path,
    parse: Callable[[Path], list[ScanFinding]],
    env: dict[str, str] | None = None,
) -> list[ScanFinding] | None:
    """The scanner's findings, or None when it could not run or its report could not be read."""
    name = cmd[0]
    try:
        proc = subprocess.run(  # noqa: S603
            cmd, cwd=workdir, env=env, capture_output=True, text=True, timeout=TIMEOUT_SECONDS, check=False
        )
        if proc.returncode != 0:
            # stderr is not logged: it may echo scanned content.
            log.warning("%s skipped: exit code %s", name, proc.returncode)
            return None
        return parse(report)
    except subprocess.TimeoutExpired:
        log.warning("%s skipped: timed out after %ss", name, TIMEOUT_SECONDS)
    except (OSError, ValueError, AttributeError, TypeError) as exc:
        log.warning("%s skipped: %s", name, type(exc).__name__)
    return None


def _gitleaks(workdir: Path, base_sha: str, head_sha: str, tmp: str) -> list[ScanFinding] | None:
    report = Path(tmp) / "gitleaks.json"
    cmd = [
        "gitleaks", "git", f"--log-opts={base_sha}..{head_sha}", "--report-format", "json",
        "--report-path", str(report), "--redact", "--exit-code", "0", str(workdir),
    ]  # fmt: skip
    # gitleaks runs git in the checkout, so it gets the trusted host git env (ruling R83).
    return _run(cmd, workdir, report, _parse_gitleaks, host_git_env(workdir))


def scan_history(workdir: Path, base_sha: str, head_sha: str) -> list[ScanFinding] | None:
    """gitleaks over the commits `base..head`, or None when it is not installed or failed: the caller then cannot
    tell a removed secret from an unscanned one."""
    if shutil.which("gitleaks") is None:
        log.info("gitleaks not installed, history not rescanned")
        return None
    with tempfile.TemporaryDirectory(prefix="reviewloop-scan-") as tmp:
        return _gitleaks(workdir, base_sha, head_sha, tmp)


def _opengrep_rules() -> Path | None:
    configured = os.environ.get(RULES_ENV)
    rules = Path(configured) if configured else DEFAULT_RULES
    return rules if rules.is_dir() else None


def run_scanners(
    names: list[str], repo: Path, tree: Path, base_sha: str, head_sha: str, changed_paths: list[str]
) -> list[ScanFinding]:
    """gitleaks reads `base_sha..head_sha` from the git history of `repo`; opengrep scans the files of `tree`, a copy
    checked out at the PR head (the CI checkout `repo` may be the base branch)."""
    findings: list[ScanFinding] = []
    with tempfile.TemporaryDirectory(prefix="reviewloop-scan-") as tmp:
        for name in names:
            if name not in ("gitleaks", "opengrep"):
                log.warning("unknown scanner %s ignored", name)
                continue
            if shutil.which(name) is None:
                log.info("%s not installed, skipped", name)
                continue
            if name == "gitleaks":
                findings += _gitleaks(repo, base_sha, head_sha, tmp) or []
            else:
                if not changed_paths:
                    log.info("opengrep skipped: no changed files")
                    continue
                rules = _opengrep_rules()
                if rules is None:
                    log.info("opengrep skipped: no rules at %s (set %s)", os.environ.get(RULES_ENV) or DEFAULT_RULES,
                             RULES_ENV)  # fmt: skip
                    continue
                report = Path(tmp) / "opengrep.sarif"
                cmd = ["opengrep", "scan", "--sarif", "--output", str(report), "--config", str(rules),
                       "--disable-version-check", "--quiet"]  # fmt: skip
                findings += _run([*cmd, "--", *changed_paths], tree, report, _parse_sarif) or []
    return findings
