import json
from pathlib import Path
from typing import Any

from reviewloop.models import Finding, Severity
from reviewloop.output.redact import redact_finding
from reviewloop.state import fingerprint

_LEVEL = {
    Severity.CRITICAL: "error",
    Severity.HIGH: "error",
    Severity.MEDIUM: "warning",
    Severity.LOW: "note",
}


def _result(f: Finding) -> dict[str, Any]:
    f = redact_finding(f)
    return {
        "ruleId": f.rule or f.category,
        "level": _LEVEL[f.severity],
        "message": {"text": f.text},
        "locations": [
            {
                "physicalLocation": {
                    "artifactLocation": {"uri": f.path},
                    "region": {"startLine": max(f.line, 1)},
                }
            }
        ],
        "partialFingerprints": {"reviewloop/v1": fingerprint(f.path, f.rule, f.existing_code)},
    }


def write_sarif(path: Path, findings: list[Finding]) -> None:
    doc = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {"driver": {"name": "reviewloop"}},
                "results": [_result(f) for f in findings],
            }
        ],
    }
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
