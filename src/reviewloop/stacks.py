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


def glob_match(pattern: str, path: str) -> bool:
    """Match a repository path against a glob where `**` spans directories and `*` stays inside one."""
    return _glob_regex(pattern).match(path) is not None


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


def _name_matches(base: str, name: str) -> bool:
    return base.endswith(name) if name.startswith(".") else base == name


def _detected(entry: str, manifests: dict[str, str]) -> bool:
    name, _, needle = entry.partition(":")
    return any(_name_matches(PurePosixPath(rel).name, name) and needle in text for rel, text in manifests.items())


def is_manifest(path: str) -> bool:
    """Whether a rule file's `detect` list names this file, so `detect_stacks` needs its content."""
    base = PurePosixPath(path).name
    names = {entry.partition(":")[0] for _, _, detect in _rule_specs().values() for entry in detect}
    return any(_name_matches(base, name) for name in names)


def detect_stacks(changed_paths: list[str], manifests: dict[str, str]) -> set[str]:
    stacks = {COMMON}
    for stack, (_, applies_to, detect) in _rule_specs().items():
        if stack == COMMON:
            continue
        touched = any(glob_match(g, p) for g in applies_to for p in changed_paths)
        if touched and any(_detected(d, manifests) for d in detect):
            stacks.add(stack)
    return stacks


def rules_for(stacks: set[str]) -> list[Path]:
    specs = _rule_specs()
    wanted = sorted(stacks - {COMMON})
    return [specs[s][0] for s in [COMMON, *wanted] if s in specs]
