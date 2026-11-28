"""Place a finding on a diff line from the verbatim code it quotes (spec section 7)."""

from collections.abc import Callable
from enum import StrEnum

from pydantic import BaseModel

from reviewloop.diff import FileDiff, changed_new_lines
from reviewloop.models import Anchor, Finding, Side

# A run of consecutive lines, as (line number, text); a quote window never spans two runs.
_Run = list[tuple[int, str]]
# A matched window, as (first line, last line).
_Window = tuple[int, int]


class Match(StrEnum):
    """Which part of the finding placed it."""

    EXISTING_CODE = "existing_code"
    CAUSE_CODE = "cause_code"
    LINE = "line"


class Placement(BaseModel):
    anchor: Anchor
    match: Match
    # The anchor is in the file the finding is about (its `path`, or that file's old name).
    own_file: bool

    def allows_suggestion(self) -> bool:
        """A suggestion replaces the anchored lines, so it fits only where the finding's own `existing_code` sits on
        the new side of its own file; a cause, another file or a bare line would replace unrelated code."""
        return self.match is Match.EXISTING_CODE and self.own_file and self.anchor.side is Side.NEW


def _exact(text: str) -> str:
    return text


def _loose(text: str) -> str:
    # Collapse every whitespace run, not only trailing: agents re-indent and re-space quotes, never change tokens.
    return " ".join(text.split())


_Norm = Callable[[str], str]
# Rules 1 to 3, in order: new side exact, new side ignoring whitespace, then the old side.
_DIFF_PASSES: tuple[tuple[Side, _Norm], ...] = (
    (Side.NEW, _exact),
    (Side.NEW, _loose),
    (Side.OLD, _exact),
    (Side.OLD, _loose),
)


def _quote_lines(quote: str) -> list[str]:
    lines = [ln.removesuffix("\r") for ln in quote.split("\n")]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def _runs(fd: FileDiff, side: Side) -> list[_Run]:
    runs: list[_Run] = []
    for hunk in fd.hunks:
        if side is Side.NEW:
            runs.append([(ln.new_no, ln.text) for ln in hunk.lines if ln.new_no is not None])
        else:
            runs.append([(ln.old_no, ln.text) for ln in hunk.lines if ln.old_no is not None])
    return runs


def _windows(quote: list[str], runs: list[_Run], norm: _Norm) -> list[_Window]:
    want = [norm(q) for q in quote]
    n = len(want)
    hits: list[_Window] = []
    for run in runs:
        texts = [norm(text) for _, text in run]
        for i in range(len(run) - n + 1):
            if texts[i : i + n] == want:
                hits.append((run[i][0], run[i + n - 1][0]))
    return hits


def _nearest(hits: list[_Window], line: int) -> _Window:
    """Pick the window whose first line is nearest to `line`; ties go to the lower line."""
    return min(hits, key=lambda w: (abs(w[0] - line), w[0]))


def _at(path: str, window: _Window, side: Side, in_diff: bool) -> Anchor:
    """Anchor on the window's last line; a multi-line window also carries its first line."""
    first, last = window
    return Anchor(path=path, line=last, start_line=first if first != last else None, side=side, in_diff=in_diff)


def _anchor_quote(
    quote: list[str],
    line: int,
    own: FileDiff | None,
    others: list[FileDiff],
    read_new_file: Callable[[str], str | None] | None,
) -> Anchor | None:
    """Rules 1 to 5 for one quote; rule 4 is skipped when `read_new_file` is None."""
    if own is not None:
        for side, norm in _DIFF_PASSES:
            hits = _windows(quote, _runs(own, side), norm)
            if hits:
                return _at(own.path, _nearest(hits, line), side, in_diff=True)
        readable = own.status != "deleted" and not own.is_binary
        text = read_new_file(own.path) if read_new_file is not None and readable else None
        if text is not None:
            run = [(no, ln.removesuffix("\r")) for no, ln in enumerate(text.split("\n"), start=1)]
            for norm in (_exact, _loose):
                hits = _windows(quote, [run], norm)
                if hits:
                    return _at(own.path, _nearest(hits, line), Side.NEW, in_diff=False)
    for side, norm in _DIFF_PASSES:
        found = [(fd.path, w) for fd in others for w in _windows(quote, _runs(fd, side), norm)]
        if len(found) == 1:
            path, window = found[0]
            return _at(path, window, side, in_diff=True)
        if found:
            return None  # ambiguous across other files
    return None


def resolve_anchor(f: Finding, diffs: list[FileDiff], read_new_file: Callable[[str], str | None]) -> Placement | None:
    """Resolve where to post `f` and how it matched, or None when the finding must go back to the agent for repair.

    `cause_code` (the changed code behind a finding on unchanged code) is tried first, then `existing_code`.
    A cause must sit in the diff, so it skips the full-file rule.
    """
    own = next((fd for fd in diffs if f.path in (fd.path, fd.old_path)), None)
    others = [fd for fd in diffs if fd is not own]
    passes = ((f.cause_code, None, Match.CAUSE_CODE), (f.existing_code, read_new_file, Match.EXISTING_CODE))
    for quote, reader, match in passes:
        lines = _quote_lines(quote or "")
        if lines:
            anchor = _anchor_quote(lines, f.line, own, others, reader)
            if anchor is not None:
                return Placement(anchor=anchor, match=match, own_file=own is not None and anchor.path == own.path)
    if own is None:
        return None
    if f.side is Side.NEW:
        changed = changed_new_lines(own)
    else:
        changed = {ln.old_no for h in own.hunks for ln in h.lines if ln.kind == "del" and ln.old_no is not None}
    if f.line not in changed:
        return None
    return Placement(
        anchor=Anchor(path=own.path, line=f.line, side=f.side, in_diff=True), match=Match.LINE, own_file=True
    )
