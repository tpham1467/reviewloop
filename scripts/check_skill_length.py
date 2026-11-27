"""Fail when a SKILL.md exceeds 800 words or a rule file exceeds 600 words (front matter excluded)."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "src" / "reviewloop"
SKILL_LIMIT = 800
RULE_LIMIT = 600


def word_count(path: Path) -> int:
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        text = text.split("---", 2)[2]
    return len(text.split())


def over_limit(root: Path = ROOT) -> list[tuple[Path, int, int]]:
    checks = [(p, SKILL_LIMIT) for p in sorted((root / "skills").glob("*/SKILL.md"))]
    checks += [(p, RULE_LIMIT) for p in sorted((root / "rules").glob("*.md"))]
    return [(p, n, limit) for p, limit in checks if (n := word_count(p)) > limit]


def main() -> int:
    bad = over_limit()
    for path, n, limit in bad:
        print(f"{path}: {n} words, limit {limit}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
