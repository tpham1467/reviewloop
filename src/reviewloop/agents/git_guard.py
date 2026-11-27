"""Allowlist guard that stands in for `git` during an agent run (ruling R17).

An installed copy of this file is the agent's `git`, first on its PATH. It runs under `python -I` with only the
standard library. It allows `git log|blame|show|diff` with a fixed set of exact options per subcommand, requires every
argument that can name a file to resolve inside the sandbox, then execs the real git with the pager, external diff
and textconv turned off. CLI deny patterns cannot do this: git accepts abbreviated long options and grouped short
options, and `git diff` silently switches to no-index mode for a path outside the repository.
"""

import os
import re
import sys
from collections.abc import Callable, Sequence


class GitDenied(Exception):
    """An argument outside the allowlist; the wrapper prints it after `reviewloop: ` and exits 2."""


# FLAG takes no value. REQUIRED takes `--name=value`, or a short option's attached value or next argument.
# OPTIONAL takes `--name=value` or an attached short value, never the next argument.
FLAG, REQUIRED, OPTIONAL = "flag", "required", "optional"
Check = Callable[[str], list[str]]
Options = dict[str, tuple[str, Check]]


def _matching(pattern: str, path_group: int | None = None) -> Check:
    compiled = re.compile(pattern)

    def check(value: str) -> list[str]:
        match = compiled.fullmatch(value)
        if match is None:
            raise ValueError(value)
        return [match.group(path_group)] if path_group else []

    return check


_NONE = _matching(r"")
_ANY = _matching(r".*")
_NUM = _matching(r"\d+")
_SCORE = _matching(r"\d+%?")
_WORD_DIFF = _matching(r"plain|color|porcelain|none")
# -L <start>[,<end>] where each bound is a line number or /regex/ (end may be +n/-n), or :<funcname>.
_RANGE_SPEC = r"(?:\d+|/[^/]*/)(?:,(?:[+-]?\d+|/[^/]*/))?|:[^:]+"
_BLAME_RANGE = _matching(_RANGE_SPEC)
_LOG_RANGE = _matching(rf"(?:{_RANGE_SPEC}):(.+)", 1)


def _flags(*names: str) -> Options:
    return dict.fromkeys(names, (FLAG, _NONE))


SUBCOMMANDS: dict[str, Options] = {
    "log": {
        **_flags("-p", "--oneline", "--stat", "--name-only", "--name-status", "--follow", "--no-merges"),
        **_flags("--first-parent", "--reverse", "--graph", "--decorate"),
        "-n": (REQUIRED, _NUM),
        "-L": (REQUIRED, _LOG_RANGE),
        "--max-count": (REQUIRED, _NUM),
        **{name: (REQUIRED, _ANY) for name in ("--format", "--pretty", "--since", "--until", "--author", "--grep")},
    },
    "blame": {
        **_flags("-w", "--porcelain", "--line-porcelain", "-s", "-e"),
        "-L": (REQUIRED, _BLAME_RANGE),
        "-M": (OPTIONAL, _SCORE),
        "-C": (OPTIONAL, _SCORE),
        "--date": (REQUIRED, _ANY),
    },
    "show": {
        **_flags("-p", "--stat", "--name-only", "--name-status"),
        "--format": (REQUIRED, _ANY),
        "--pretty": (REQUIRED, _ANY),
        "-U": (REQUIRED, _NUM),
    },
    "diff": {
        **_flags("--stat", "--name-only", "--name-status", "--cached", "--staged", "-w", "--ignore-space-change"),
        **_flags("--function-context"),
        "-U": (REQUIRED, _NUM),
        "--word-diff": (OPTIONAL, _WORD_DIFF),
    },
}
SAFE_FLAGS = {"blame": ["--no-textconv"]}
DIFF_SAFE_FLAGS = ["--no-ext-diff", "--no-textconv"]

_RANGE = re.compile(r"(.*?)(\.\.\.?)(.*)")
# A revision side of `a..b`: starts like a ref (never `/`, `~` or `.`), never contains `..` or a `/.` segment.
_REV = re.compile(r"[\w@{}^+-][\w@{}^~+/.-]*")


def _is_rev(side: str) -> bool:
    if not side:
        return True
    unsafe = ".." in side or "/." in side or "//" in side or side.endswith(("/", "."))
    return bool(_REV.fullmatch(side)) and not unsafe


def _is_range(arg: str) -> bool:
    match = _RANGE.fullmatch(arg)
    return bool(match and (match[1] or match[3]) and _is_rev(match[1]) and _is_rev(match[3]))


class _Checker:
    def __init__(self, root: str, cwd: str) -> None:
        self.root = os.path.realpath(root)
        self.cwd = os.path.realpath(cwd)
        if not self._inside(self.cwd):
            raise GitDenied("git must run inside the sandbox")

    def _inside(self, real: str) -> bool:
        return real == self.root or real.startswith(self.root + os.sep)

    def path(self, value: str, base: str | None = None) -> None:
        if value.startswith("~") or os.path.isabs(value):
            raise GitDenied(f"git path outside the sandbox: {value}")
        if not self._inside(os.path.realpath(os.path.join(base or self.cwd, value))):
            raise GitDenied(f"git path outside the sandbox: {value}")

    def positional(self, arg: str) -> None:
        if _is_range(arg):
            return
        # The whole argument may be read as a file (diff falls back to no-index for paths outside the repository),
        # so check it as a path first, even when it also looks like `<rev>:<path>`.
        if "/" in arg or ":" in arg or arg.startswith("~") or self.on_disk(arg):
            self.path(arg)
        if ":" in arg:
            # <rev>:<path> and :<stage>:<path> name a path from the repository root, ./ and ../ from the cwd.
            rest = re.sub(r"^\d:", "", arg[1:]) if arg.startswith(":") else arg.split(":", 1)[1]
            self.path(rest, None if rest.startswith(("./", "../")) else self.root)

    def on_disk(self, arg: str) -> bool:
        return os.path.lexists(os.path.join(self.cwd, arg))

    def value(self, check: Check, value: str, shown: str) -> None:
        try:
            paths = check(value)
        except ValueError:
            raise GitDenied(f"git option value not allowed: {shown}") from None
        for path in paths:
            self.path(path)


def _short_group(arg: str, options: Options, rest: list[str], checker: _Checker) -> None:
    for j in range(1, len(arg)):
        name = "-" + arg[j]
        if name not in options:
            raise GitDenied(f"git option not allowed: {name}")
        kind, check = options[name]
        if kind == FLAG:
            continue
        value = arg[j + 1 :]
        if kind == REQUIRED and not value:
            if not rest:
                raise GitDenied(f"git option needs a value: {name}")
            value = rest.pop(0)
        if value:
            checker.value(check, value, name + value)
        return


def git_command(args: Sequence[str], *, real_git: str, root: str, cwd: str) -> list[str]:
    """Validate an agent's git arguments and return the real git argv, or raise `GitDenied`."""
    checker = _Checker(root, cwd)
    if not args:
        raise GitDenied("git subcommand not allowed: (none)")
    sub, rest = args[0], list(args[1:])
    if sub.startswith("-"):
        raise GitDenied(f"git option not allowed: {sub}")
    if sub not in SUBCOMMANDS:
        raise GitDenied(f"git subcommand not allowed: {sub}")
    options = SUBCOMMANDS[sub]
    pending = list(rest)
    while pending:
        arg = pending.pop(0)
        if arg == "--":
            for path in pending:
                checker.path(path)
            break
        if arg.startswith("--"):
            name, eq, value = arg.partition("=")
            if name not in options:
                raise GitDenied(f"git option not allowed: {name}")
            kind, check = options[name]
            if kind == FLAG and eq:
                raise GitDenied(f"git option takes no value: {arg}")
            if kind == REQUIRED and not eq:
                raise GitDenied(f"git option needs =<value>: {name}")
            if eq:
                checker.value(check, value, arg)
        elif arg == "-":
            raise GitDenied("git option not allowed: -")
        elif arg.startswith("-"):
            _short_group(arg, options, pending, checker)
        else:
            checker.positional(arg)
            # Without `--`, git diff treats two paths as a no-index diff of arbitrary files. Revisions never name a
            # file in the working tree, so a diff argument that exists on disk must come after `--`.
            if sub == "diff" and checker.on_disk(arg):
                raise GitDenied(f"git diff paths must follow --: {arg}")
    # --literal-pathspecs turns off pathspec magic such as `:(top)` and `:(glob)`, so a path means what it says.
    return [real_git, "--no-pager", "--literal-pathspecs", sub, *SAFE_FLAGS.get(sub, DIFF_SAFE_FLAGS), *rest]


def git_wrapper_main(argv: Sequence[str], *, real_git: str, root: str) -> int:
    try:
        command = git_command(argv, real_git=real_git, root=root, cwd=os.getcwd())
    except GitDenied as exc:
        print(f"reviewloop: {exc}", file=sys.stderr)
        return 2
    # Any GIT_* variable (GIT_DIR, GIT_EXTERNAL_DIFF, GIT_CONFIG_PARAMETERS, ...) could redirect or extend git.
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_PAGER="cat")
    os.execve(real_git, command, env)  # noqa: S606
    return 0
