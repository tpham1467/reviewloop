"""Parse unified diffs (git or plain) into files, hunks and numbered lines."""

import re
from dataclasses import dataclass, field
from typing import Literal

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_C_ESCAPES = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13, '"': 34, "\\": 92}


@dataclass
class DiffLine:
    kind: Literal["add", "del", "ctx"]
    old_no: int | None
    new_no: int | None
    text: str


@dataclass
class Hunk:
    old_start: int
    new_start: int
    lines: list[DiffLine] = field(default_factory=list[DiffLine])


@dataclass
class FileDiff:
    path: str
    old_path: str | None
    status: Literal["added", "modified", "deleted", "renamed"]
    is_binary: bool
    mode_only: bool
    hunks: list[Hunk]


@dataclass
class _Builder:
    old: str | None = None
    new: str | None = None
    status: Literal["added", "modified", "deleted", "renamed"] = "modified"
    is_binary: bool = False
    mode_change: bool = False
    seen_file_header: bool = False
    hunks: list[Hunk] = field(default_factory=list[Hunk])

    def build(self) -> FileDiff:
        path = self.old if self.status == "deleted" else self.new
        return FileDiff(
            path=path or self.old or "",
            old_path=self.old if self.status == "renamed" else None,
            status=self.status,
            is_binary=self.is_binary,
            mode_only=self.mode_change and not self.hunks and not self.is_binary and self.status == "modified",
            hunks=self.hunks,
        )


def _unquote(token: str) -> str:
    """Decode a git C-style quoted path such as "a/caf\\303\\251.md" (octal bytes are UTF-8)."""
    if not token.startswith('"'):
        return token
    out = bytearray()
    i, body = 0, token[1:-1]
    while i < len(body):
        ch = body[i]
        if ch == "\\" and i + 1 < len(body):
            nxt = body[i + 1]
            if nxt in "01234567":
                out.append(int(body[i + 1 : i + 4], 8))
                i += 4
                continue
            out.append(_C_ESCAPES.get(nxt, ord(nxt)))
            i += 2
            continue
        out.extend(ch.encode())
        i += 1
    return out.decode("utf-8", errors="replace")


def _strip_prefix(path: str) -> str | None:
    if path == "/dev/null":
        return None
    return path[2:] if path[:2] in ("a/", "b/") else path


def _split_git_header(rest: str) -> tuple[str, str]:
    """Split the `a/X b/Y` part of `diff --git`; unquoted names may contain spaces."""
    if rest.startswith('"'):
        end = re.match(r'"(?:[^"\\]|\\.)*"', rest)
        first = end.group(0) if end else rest
        return _unquote(first), _unquote(rest[len(first) :].lstrip())
    # Same name on both sides (the common case): "a/P b/P" has length 2 * len(P) + 5.
    half = (len(rest) - 5) // 2
    if rest[2 + half : 5 + half] == " b/" and rest[2 : 2 + half] == rest[5 + half :]:
        return rest[: 2 + half], rest[3 + half :]
    old, _, new = rest.partition(" b/")
    return old, "b/" + new


def _header_path(line: str) -> str:
    # git appends a tab to names with spaces; plain diff puts a timestamp after a tab.
    return _unquote(line[4:].split("\t", 1)[0])


def parse_unified_diff(text: str) -> list[FileDiff]:
    lines = [ln.removesuffix("\r") for ln in text.split("\n")]
    if lines[-1] == "":
        lines.pop()  # the final newline ends the last line; it does not start an empty one
    files: list[_Builder] = []
    cur: _Builder | None = None
    hunk: Hunk | None = None
    old_left = new_left = old_no = new_no = 0

    for i, line in enumerate(lines):
        if hunk is not None and (old_left > 0 or new_left > 0):
            tag, body = line[:1], line[1:]
            if tag in (" ", ""):
                hunk.lines.append(DiffLine("ctx", old_no, new_no, body))
                old_no, new_no, old_left, new_left = old_no + 1, new_no + 1, old_left - 1, new_left - 1
                continue
            if tag == "-":
                hunk.lines.append(DiffLine("del", old_no, None, body))
                old_no, old_left = old_no + 1, old_left - 1
                continue
            if tag == "+":
                hunk.lines.append(DiffLine("add", None, new_no, body))
                new_no, new_left = new_no + 1, new_left - 1
                continue
            if tag == "\\":
                continue
            hunk = None  # malformed or truncated hunk: fall through and parse as a header line

        if line.startswith("diff --git "):
            old, new = _split_git_header(line[len("diff --git ") :])
            cur = _Builder(old=_strip_prefix(old), new=_strip_prefix(new))
            files.append(cur)
            hunk = None
        elif line.startswith("--- ") and i + 1 < len(lines) and lines[i + 1].startswith("+++ "):
            if cur is None or cur.seen_file_header or cur.hunks:
                cur = _Builder()
                files.append(cur)
            cur.seen_file_header = True
            cur.old = _strip_prefix(_header_path(line))
            cur.new = _strip_prefix(_header_path(lines[i + 1]))
            if cur.old is None:
                cur.status = "added"
            elif cur.new is None:
                cur.status = "deleted"
            hunk = None
        elif cur is None:
            continue
        elif m := _HUNK_RE.match(line):
            old_no, new_no = int(m.group(1)), int(m.group(3))
            old_left = int(m.group(2)) if m.group(2) is not None else 1
            new_left = int(m.group(4)) if m.group(4) is not None else 1
            hunk = Hunk(old_start=old_no, new_start=new_no)
            cur.hunks.append(hunk)
            # A zero-length side reports the line before the change; numbering starts after it.
            old_no, new_no = max(old_no, 1), max(new_no, 1)
        elif line.startswith("new file mode"):
            cur.status = "added"
        elif line.startswith("deleted file mode"):
            cur.status = "deleted"
        elif line.startswith("rename from "):
            cur.status, cur.old = "renamed", _unquote(line[len("rename from ") :])
        elif line.startswith("rename to "):
            cur.status, cur.new = "renamed", _unquote(line[len("rename to ") :])
        elif line.startswith(("old mode", "new mode")):
            cur.mode_change = True
        elif line.startswith("Binary files ") or line == "GIT binary patch":
            cur.is_binary = True

    return [b.build() for b in files]


def changed_new_lines(fd: FileDiff) -> set[int]:
    return {ln.new_no for h in fd.hunks for ln in h.lines if ln.kind == "add" and ln.new_no is not None}
