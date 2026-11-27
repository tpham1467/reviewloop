import json
from pathlib import Path

from reviewloop.models import FileCoverage, Finding
from reviewloop.output.redact import redact_finding


def write_json(path: Path, findings: list[Finding], coverage: list[FileCoverage]) -> None:
    doc = {
        "findings": [redact_finding(f).model_dump(mode="json") for f in findings],
        "coverage": [c.model_dump(mode="json") for c in coverage],
    }
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
