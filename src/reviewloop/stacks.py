"""Stack detection and rule-file lookup, driven by each rule file's front matter."""

import re
from functools import cache
from pathlib import Path, PurePosixPath
from typing import cast

import yaml

RULES_DIR = Path(__file__).parent / "rules"
COMMON = "common"


@cache
def _glob_regex(pattern: str) -> re.Pattern[str]:
    out: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def _front_matter(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    _, header, _ = text.split("---", 2)
    data: dict[str, object] = yaml.safe_load(header)
    return data


def _string_list(path: Path, meta: dict[str, object], key: str) -> list[str]:
    value = meta.get(key)
    if not isinstance(value, list) or not all(isinstance(v, str) for v in cast("list[object]", value)):
        raise ValueError(f"{path.name}: front matter {key!r} must be a list of strings")
    return cast("list[str]", value)


@cache
def _rule_specs() -> dict[str, tuple[Path, list[str], list[str]]]:
    specs: dict[str, tuple[Path, list[str], list[str]]] = {}
    for path in sorted(RULES_DIR.glob("*.md")):
        meta = _front_matter(path)
        stack = meta.get("stack")
        if not isinstance(stack, str):
            raise ValueError(f"{path.name}: front matter 'stack' must be a string")
        specs[stack] = (path, _string_list(path, meta, "applies_to"), _string_list(path, meta, "detect"))
    return specs


def _detected(entry: str, manifests: dict[str, str]) -> bool:
    name, _, needle = entry.partition(":")
    for rel, text in manifests.items():
        base = PurePosixPath(rel).name
        hit = base.endswith(name) if name.startswith(".") else base == name
        if hit and needle in text:
            return True
    return False


def detect_stacks(changed_paths: list[str], manifests: dict[str, str]) -> set[str]:
    stacks = {COMMON}
    for stack, (_, applies_to, detect) in _rule_specs().items():
        if stack == COMMON:
            continue
        touched = any(_glob_regex(g).match(p) for g in applies_to for p in changed_paths)
        if touched and any(_detected(d, manifests) for d in detect):
            stacks.add(stack)
    return stacks


def rules_for(stacks: set[str]) -> list[Path]:
    specs = _rule_specs()
    wanted = sorted(stacks - {COMMON})
    return [specs[s][0] for s in [COMMON, *wanted] if s in specs]
