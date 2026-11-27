"""AACR-Bench subset (Apache-2.0, https://github.com/alibaba/aacr-bench), downloaded at run time, never committed.

The dataset holds real GitHub PRs with human-verified review comments. Each PR becomes one eval case: the repository
is cloned and checked out at the PR's source commit, and each comment is an expected finding.
"""

import json
import os
import urllib.request
from dataclasses import dataclass

from score import Expected

DEFAULT_URL = "https://raw.githubusercontent.com/alibaba/aacr-bench/main/dataset/v2/positive_samples.json"
URL_ENV = "AACR_URL"
# AACR labels map onto the reviewloop categories; "Code Defect" has no finer label, so it maps to `correctness`.
CATEGORY_MAP = {
    "Code Defect": "correctness",
    "Security Vulnerability": "security",
    "Performance": "performance",
    "Maintainability and Readability": "maintainability",
}
MAX_BYTES = 20_000_000


@dataclass(frozen=True)
class AacrCase:
    name: str
    repo_url: str
    base_commit: str
    head_commit: str
    expected: list[Expected]


def fetch(url: str) -> list[dict[str, object]]:
    if not url.startswith("https://"):
        raise ValueError("AACR url must use https")
    with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310 - https only, checked above
        data = json.loads(resp.read(MAX_BYTES))
    if not isinstance(data, list):
        raise ValueError("AACR dataset must be a JSON list")
    return data  # type: ignore[return-value]


def parse(raw: list[dict[str, object]], limit: int, max_changed_lines: int) -> list[AacrCase]:
    """The first `limit` PRs of at most `max_changed_lines` changed lines whose comments all map to a category."""
    cases: list[AacrCase] = []
    for pr in raw:
        pr_url = str(pr["githubPrUrl"])
        owner, repo, _, number = pr_url.removeprefix("https://github.com/").split("/")
        comments: list[dict[str, object]] = pr["comments"]  # type: ignore[assignment]
        expected = [
            Expected(path=str(c["path"]), line=int(c["from_line"]), category=CATEGORY_MAP[str(c["category"])])  # type: ignore[call-overload]
            for c in comments
            if str(c["category"]) in CATEGORY_MAP and c.get("side", "right") == "right"
        ]
        if not expected or int(pr["change_line_count"]) > max_changed_lines:  # type: ignore[call-overload]
            continue
        cases.append(
            AacrCase(
                name=f"aacr-{owner}-{repo}-{number}",
                repo_url=f"https://github.com/{owner}/{repo}.git",
                base_commit=str(pr["target_commit"]),
                head_commit=str(pr["source_commit"]),
                expected=expected,
            )
        )
        if len(cases) == limit:
            break
    return cases


def load_subset(limit: int = 20, max_changed_lines: int = 300) -> list[AacrCase]:
    """Empty list when AACR_URL is set to an empty string; the default is the public Apache-2.0 dataset."""
    url = os.environ.get(URL_ENV, DEFAULT_URL)
    if not url:
        return []
    return parse(fetch(url), limit, max_changed_lines)
