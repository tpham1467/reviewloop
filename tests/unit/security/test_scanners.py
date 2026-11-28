import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from reviewloop.security import scanners
from reviewloop.security.scanners import ScanFinding, run_scanners

FAKE_SECRET = "ghp_" + "A" * 36  # built at runtime so no token-shaped literal is in the source

GITLEAKS_JSON = [
    {
        "RuleID": "github-pat",
        "Description": "GitHub Personal Access Token",
        "File": "src/config.py",
        "StartLine": 12,
        "Secret": FAKE_SECRET,
        "Match": f"token = {FAKE_SECRET}",
        "Commit": "abc123",
    }
]

SARIF = {
    "version": "2.1.0",
    "runs": [
        {
            "results": [
                {
                    "ruleId": "python.lang.security.audit.eval-injection",
                    "message": {"text": "Detected eval of user input."},
                    "locations": [
                        {"physicalLocation": {"artifactLocation": {"uri": "app/views.py"}, "region": {"startLine": 7}}}
                    ],
                },
                {"ruleId": "no-location", "message": {"text": "No location."}, "locations": []},
            ]
        }
    ],
}


def _fake_run(calls: list[list[str]], gitleaks: Any = None, sarif: Any = None, returncode: int = 0) -> Any:
    def run(cmd: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if cmd[0] == "gitleaks" and gitleaks is not None:
            Path(cmd[cmd.index("--report-path") + 1]).write_text(json.dumps(gitleaks))
        if cmd[0] == "opengrep" and sarif is not None:
            Path(cmd[cmd.index("--output") + 1]).write_text(json.dumps(sarif))
        return subprocess.CompletedProcess(cmd, returncode, "", "")

    return run


def _installed(monkeypatch: pytest.MonkeyPatch, *names: str) -> None:
    monkeypatch.setattr(scanners.shutil, "which", lambda n: f"/bin/{n}" if n in names else None)


def test_missing_scanner_skipped(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, tmp_path: Path):
    _installed(monkeypatch)
    calls: list[list[str]] = []
    monkeypatch.setattr(scanners.subprocess, "run", _fake_run(calls))
    with caplog.at_level("INFO", logger="reviewloop.security"):
        out = run_scanners(["opengrep", "gitleaks"], tmp_path, tmp_path, "a", "b", ["x.py"])
    assert out == []
    assert calls == []
    assert "not installed" in caplog.text


def test_parse_opengrep_sarif(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _installed(monkeypatch, "opengrep")
    calls: list[list[str]] = []
    monkeypatch.setattr(scanners.subprocess, "run", _fake_run(calls, sarif=SARIF))
    out = run_scanners(["opengrep"], tmp_path, tmp_path, "a", "b", ["app/views.py"])
    assert out == [
        ScanFinding(
            "opengrep",
            "python.lang.security.audit.eval-injection",
            "app/views.py",
            7,
            "Detected eval of user input.",
            False,
        ),
        ScanFinding("opengrep", "no-location", "", None, "No location.", False),
    ]
    assert calls[0][-1] == "app/views.py"


def test_opengrep_skipped_without_changed_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _installed(monkeypatch, "opengrep")
    calls: list[list[str]] = []
    monkeypatch.setattr(scanners.subprocess, "run", _fake_run(calls, sarif=SARIF))
    assert run_scanners(["opengrep"], tmp_path, tmp_path, "a", "b", []) == []
    assert calls == []


def test_parse_gitleaks_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _installed(monkeypatch, "gitleaks")
    calls: list[list[str]] = []
    monkeypatch.setattr(scanners.subprocess, "run", _fake_run(calls, gitleaks=GITLEAKS_JSON))
    out = run_scanners(["gitleaks"], tmp_path, tmp_path, "base", "head", [])
    assert out == [ScanFinding("gitleaks", "github-pat", "src/config.py", 12, "GitHub Personal Access Token", True)]
    assert "--log-opts=base..head" in calls[0]
    assert "--redact" in calls[0]


def test_gitleaks_secret_value_never_in_message(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _installed(monkeypatch, "gitleaks")
    monkeypatch.setattr(scanners.subprocess, "run", _fake_run([], gitleaks=GITLEAKS_JSON))
    out = run_scanners(["gitleaks"], tmp_path, tmp_path, "base", "head", [])
    assert FAKE_SECRET not in repr(out)


@pytest.mark.parametrize("failure", ["error", "timeout"])
def test_scanner_failure_is_skipped(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, tmp_path: Path, failure: str
):
    _installed(monkeypatch, "gitleaks", "opengrep")

    def run(cmd: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
        if failure == "timeout":
            raise subprocess.TimeoutExpired(cmd, 90)
        return subprocess.CompletedProcess(cmd, 2, "", f"boom {FAKE_SECRET}")

    monkeypatch.setattr(scanners.subprocess, "run", run)
    with caplog.at_level("WARNING", logger="reviewloop.security"):
        assert run_scanners(["gitleaks", "opengrep"], tmp_path, tmp_path, "a", "b", ["x.py"]) == []
    assert "skipped" in caplog.text
    assert FAKE_SECRET not in caplog.text


@pytest.mark.parametrize(("tool", "flag"), [("gitleaks", "--report-path"), ("opengrep", "--output")])
def test_garbage_report_is_skipped(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, tool: str, flag: str):
    _installed(monkeypatch, tool)

    def run(cmd: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
        Path(cmd[cmd.index(flag) + 1]).write_text("not json")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(scanners.subprocess, "run", run)
    assert run_scanners([tool], tmp_path, tmp_path, "a", "b", ["x.py"]) == []


def test_scanner_without_report_does_not_read_other_report(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _installed(monkeypatch, "gitleaks", "opengrep")

    def run(cmd: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
        if cmd[0] == "gitleaks":
            Path(cmd[cmd.index("--report-path") + 1]).write_text(json.dumps(GITLEAKS_JSON))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(scanners.subprocess, "run", run)
    out = run_scanners(["gitleaks", "opengrep"], tmp_path, tmp_path, "a", "b", ["x.py"])
    assert [f.tool for f in out] == ["gitleaks"]


def test_opengrep_paths_follow_double_dash(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _installed(monkeypatch, "opengrep")
    calls: list[list[str]] = []
    monkeypatch.setattr(scanners.subprocess, "run", _fake_run(calls, sarif=SARIF))
    evil = "--config=https://evil.example/rules.yml"
    run_scanners(["opengrep"], tmp_path, tmp_path, "a", "b", [evil])
    assert calls[0][calls[0].index(evil) - 1] == "--"


def test_unknown_scanner_ignored(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _installed(monkeypatch, "nope")
    assert run_scanners(["nope"], tmp_path, tmp_path, "a", "b", ["x"]) == []


def test_scan_history_runs_gitleaks_on_the_range(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from reviewloop.security.scanners import scan_history

    _installed(monkeypatch, "gitleaks")
    calls: list[list[str]] = []
    monkeypatch.setattr(scanners.subprocess, "run", _fake_run(calls, gitleaks=GITLEAKS_JSON))
    out = scan_history(tmp_path, "base", "head")
    assert out is not None and [f.rule for f in out] == ["github-pat"]
    assert "--log-opts=base..head" in calls[0]
    monkeypatch.setattr(scanners.subprocess, "run", _fake_run([], gitleaks=[]))
    assert scan_history(tmp_path, "base", "head") == []


def test_scan_history_unavailable_is_none(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from reviewloop.security.scanners import scan_history

    _installed(monkeypatch)
    assert scan_history(tmp_path, "a", "b") is None
    _installed(monkeypatch, "gitleaks")
    monkeypatch.setattr(scanners.subprocess, "run", _fake_run([], returncode=2))
    assert scan_history(tmp_path, "a", "b") is None


def test_opengrep_scans_tree_gitleaks_scans_repo(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _installed(monkeypatch, "gitleaks", "opengrep")
    cwds: dict[str, Path] = {}
    trust: dict[str, str | None] = {}

    def run(cmd: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        cwds[cmd[0]] = kw["cwd"]
        trust[cmd[0]] = (kw["env"] or {}).get("GIT_CONFIG_VALUE_0")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(scanners.subprocess, "run", run)
    repo, tree = tmp_path / "checkout", tmp_path / "sandbox-security"
    run_scanners(["gitleaks", "opengrep"], repo, tree, "a", "b", ["x.py"])
    assert cwds == {"gitleaks": repo, "opengrep": tree}
    # gitleaks runs git in the checkout, which is trusted; opengrep reads the head copy and gets no git trust.
    assert trust == {"gitleaks": str(repo.resolve()), "opengrep": None}
