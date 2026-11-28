from collections.abc import Callable
from pathlib import Path

from reviewloop.anchor import Match, Placement, resolve_anchor
from reviewloop.diff import FileDiff, parse_unified_diff
from reviewloop.models import Anchor, Finding, Side

FIXTURES = Path(__file__).parent.parent / "fixtures" / "diffs"

COMPUTE_NEW = "        result = compute(data, strict=True)"


def _diffs(name: str = "anchor.diff") -> list[FileDiff]:
    return parse_unified_diff((FIXTURES / name).read_text(encoding="utf-8"))


def _finding(existing_code: str, line: int, path: str = "app/service.py", **kw: object) -> Finding:
    data: dict[str, object] = {
        "path": path,
        "line": line,
        "severity": "medium",
        "category": "bug",
        "text": "t",
        "existing_code": existing_code,
    }
    data.update(kw)
    return Finding.model_validate(data)


def _anchor(f: Finding, diffs: list[FileDiff], read: Callable[[str], str | None]) -> Anchor | None:
    placed = resolve_anchor(f, diffs, read)
    return None if placed is None else placed.anchor


def _no_files(_path: str) -> str | None:
    return None


def _files(files: dict[str, str]) -> Callable[[str], str | None]:
    return files.get


def test_anchor_exact_new_side_nearest_line() -> None:
    diffs = _diffs()
    assert _anchor(_finding(COMPUTE_NEW, 120), diffs, _no_files) == Anchor(
        path="app/service.py", line=117, side=Side.NEW, in_diff=True
    )
    assert _anchor(_finding(COMPUTE_NEW, 135), diffs, _no_files) == Anchor(
        path="app/service.py", line=140, side=Side.NEW, in_diff=True
    )


def test_anchor_nearest_tie_goes_to_lower_line() -> None:
    text = "diff --git a/t.py b/t.py\n--- a/t.py\n+++ b/t.py\n@@ -1,0 +1,5 @@\n+x\n+a\n+b\n+c\n+x\n"
    anchor = _anchor(_finding("x", 3, path="t.py"), parse_unified_diff(text), _no_files)
    assert anchor == Anchor(path="t.py", line=1, side=Side.NEW, in_diff=True)


def test_anchor_multiline_quote_uses_last_line() -> None:
    # The window is chosen by its first line nearest to `line`; the anchor is the window's last line.
    quote = "        data = self.fetch()\n" + COMPUTE_NEW + "\n"
    diffs = _diffs()
    assert _anchor(_finding(quote, 116), diffs, _no_files) == Anchor(
        path="app/service.py", line=117, start_line=116, side=Side.NEW, in_diff=True
    )
    assert _anchor(_finding(quote, 139), diffs, _no_files) == Anchor(
        path="app/service.py", line=140, start_line=139, side=Side.NEW, in_diff=True
    )
    three = "    def run(self):\n        data = self.fetch()\n" + COMPUTE_NEW
    assert _anchor(_finding(three, 115), diffs, _no_files) == Anchor(
        path="app/service.py", line=117, start_line=115, side=Side.NEW, in_diff=True
    )


def test_anchor_start_line_counts_matched_window_not_quote() -> None:
    # Blank edge lines in the quote are dropped, so the range is two lines, not four.
    quote = "\n        data = self.fetch()\n" + COMPUTE_NEW + "\n\n"
    assert _anchor(_finding(quote, 116), _diffs(), _no_files) == Anchor(
        path="app/service.py", line=117, start_line=116, side=Side.NEW, in_diff=True
    )
    # Single-line quotes leave start_line unset.
    assert _anchor(_finding(COMPUTE_NEW, 117), _diffs(), _no_files) == Anchor(
        path="app/service.py", line=117, side=Side.NEW, in_diff=True
    )


def test_anchor_window_does_not_cross_hunks() -> None:
    quote = "    def stop(self):\n    def retry(self):"
    assert _anchor(_finding(quote, 9999), _diffs(), _no_files) is None


def test_anchor_whitespace_insensitive() -> None:
    diffs = _diffs()
    for quote in ("result = compute(data,  strict=True)   ", "\tresult  =  compute(data, strict=True)\n"):
        assert _anchor(_finding(quote, 117), diffs, _no_files) == Anchor(
            path="app/service.py", line=117, side=Side.NEW, in_diff=True
        )


def test_anchor_exact_pass_wins_over_whitespace_pass() -> None:
    text = "diff --git a/w.py b/w.py\n--- a/w.py\n+++ b/w.py\n@@ -0,0 +1,3 @@\n+if a:\n+    go()\n+go()\n"
    anchor = _anchor(_finding("go()", 2, path="w.py"), parse_unified_diff(text), _no_files)
    assert anchor == Anchor(path="w.py", line=3, side=Side.NEW, in_diff=True)


def test_anchor_old_side_removed_line() -> None:
    diffs = _diffs()
    assert _anchor(_finding("        result = compute(data)", 117), diffs, _no_files) == Anchor(
        path="app/service.py", line=117, side=Side.OLD, in_diff=True
    )
    removed_with_context = "    payload = build()\n    send(payload)"
    assert _anchor(_finding(removed_with_context, 10, path="app/client.py"), diffs, _no_files) == Anchor(
        path="app/client.py", line=11, start_line=10, side=Side.OLD, in_diff=True
    )


def test_anchor_full_file_outside_diff() -> None:
    lines = [f"# line {n}" for n in range(1, 151)]
    lines[29] = "def helper():"
    lines[116] = COMPUTE_NEW
    reader = _files({"app/service.py": "\r\n".join(lines) + "\r\n"})
    assert _anchor(_finding("def helper():", 20), _diffs(), reader) == Anchor(
        path="app/service.py", line=30, side=Side.NEW, in_diff=False
    )
    assert _anchor(_finding("  def   helper():", 20), _diffs(), reader) == Anchor(
        path="app/service.py", line=30, side=Side.NEW, in_diff=False
    )


def test_anchor_full_file_match_is_file_level_even_when_ending_inside_hunk() -> None:
    lines = [f"# line {n}" for n in range(1, 151)]
    lines[113] = "    # before run"
    lines[114] = "    def run(self):"
    reader = _files({"app/service.py": "\n".join(lines)})
    quote = "    # before run\n    def run(self):"
    assert _anchor(_finding(quote, 114), _diffs(), reader) == Anchor(
        path="app/service.py", line=115, start_line=114, side=Side.NEW, in_diff=False
    )


def test_anchor_full_file_skipped_when_unavailable_or_deleted() -> None:
    reader = _files({"app/other.py": "def helper():\n"})
    assert _anchor(_finding("def helper():", 1), _diffs(), _no_files) is None
    deleted = _diffs("deleted.diff")
    gone_reader = _files({"old/gone.py": "z = 3\n"})
    assert _anchor(_finding("z = 3", 1, path="old/gone.py"), deleted, gone_reader) is None
    assert _anchor(_finding("def helper():", 1, path="app/other.py"), _diffs(), reader) is None


def test_anchor_other_file_unique_match_only() -> None:
    diffs = _diffs()
    assert _anchor(_finding("payload = build()", 3), diffs, _no_files) == Anchor(
        path="app/client.py", line=10, side=Side.NEW, in_diff=True
    )
    assert _anchor(_finding("send(payload)", 3), diffs, _no_files) == Anchor(
        path="app/client.py", line=11, side=Side.OLD, in_diff=True
    )
    # "return payload" sits in both app/client.py and app/util.py: ambiguous, so no anchor.
    assert _anchor(_finding("    return payload", 3), diffs, _no_files) is None


def test_anchor_fallback_to_changed_line() -> None:
    diffs = _diffs()
    assert _anchor(_finding("not in the code", 118), diffs, _no_files) == Anchor(
        path="app/service.py", line=118, side=Side.NEW, in_diff=True
    )
    assert _anchor(_finding("not in the code", 117, side="old"), diffs, _no_files) == Anchor(
        path="app/service.py", line=117, side=Side.OLD, in_diff=True
    )
    assert _anchor(_finding("", 140), diffs, _no_files) == Anchor(
        path="app/service.py", line=140, side=Side.NEW, in_diff=True
    )
    # Context line, and a removed line number asked on the new side: not changed lines on that side.
    assert _anchor(_finding("not in the code", 119), diffs, _no_files) is None
    assert _anchor(_finding("not in the code", 141, side="old"), diffs, _no_files) is None


def test_anchor_none_when_nothing_matches() -> None:
    diffs = _diffs()
    assert _anchor(_finding("never = present()", 500), diffs, _no_files) is None
    assert _anchor(_finding("never = present()", 5, path="missing.py"), diffs, _no_files) is None


def test_anchor_uses_cause_code() -> None:
    diffs = _diffs()
    caller = _finding(
        "    wrap(payload)",
        42,
        path="app/caller.py",
        cause_code="    send(payload, timeout=5)",
    )
    assert _anchor(caller, diffs, _no_files) == Anchor(path="app/client.py", line=11, side=Side.NEW, in_diff=True)
    # Cause wins over an existing_code match in the finding's own file.
    same_file = _finding("        self.log(result)", 118, cause_code=COMPUTE_NEW)
    assert _anchor(same_file, diffs, _no_files) == Anchor(path="app/service.py", line=117, side=Side.NEW, in_diff=True)


def test_anchor_multiline_cause_code_in_other_file_sets_start_line() -> None:
    caller = _finding(
        "    wrap(payload)", 42, path="app/caller.py", cause_code="    payload = build()\n    send(payload)"
    )
    assert _anchor(caller, _diffs(), _no_files) == Anchor(
        path="app/client.py", line=11, start_line=10, side=Side.OLD, in_diff=True
    )


def test_anchor_cause_code_skips_full_file() -> None:
    # A cause must be changed code: a full-file match does not count, so existing_code decides.
    reader = _files({"app/service.py": "def helper():\n"})
    finding = _finding("        self.log(result)", 1, cause_code="def helper():")
    assert _anchor(finding, _diffs(), reader) == Anchor(path="app/service.py", line=118, side=Side.NEW, in_diff=True)
    finding = _finding("def helper():", 1, cause_code="def helper():")
    assert _anchor(finding, _diffs(), reader) == Anchor(path="app/service.py", line=1, side=Side.NEW, in_diff=False)


def test_anchor_cause_code_falls_back_to_existing_code() -> None:
    finding = _finding("        self.log(result)", 1, cause_code="nothing like this")
    assert _anchor(finding, _diffs(), _no_files) == Anchor(path="app/service.py", line=118, side=Side.NEW, in_diff=True)


def test_anchor_crlf_and_no_newline() -> None:
    assert _anchor(_finding("beta\r\ngamma\r\n", 3, path="cfg.txt"), _diffs("no_newline.diff"), _no_files) == (
        Anchor(path="cfg.txt", line=3, start_line=2, side=Side.NEW, in_diff=True)
    )
    crlf = "diff --git a/w.txt b/w.txt\n--- a/w.txt\n+++ b/w.txt\n@@ -1 +1,2 @@\n one\r\n+two\r\n"
    assert _anchor(_finding("one\ntwo", 1, path="w.txt"), parse_unified_diff(crlf), _no_files) == Anchor(
        path="w.txt", line=2, start_line=1, side=Side.NEW, in_diff=True
    )


def test_anchor_renamed_file() -> None:
    diffs = _diffs("renamed.diff")
    for path in ("pkg/new_name.py", "pkg/old_name.py"):
        assert _anchor(_finding('def load(path, mode="r"):', 2, path=path), diffs, _no_files) == Anchor(
            path="pkg/new_name.py", line=2, side=Side.NEW, in_diff=True
        )
    assert _anchor(_finding("def load(path):", 2, path="pkg/old_name.py"), diffs, _no_files) == Anchor(
        path="pkg/new_name.py", line=2, side=Side.OLD, in_diff=True
    )


def test_placement_reports_how_it_matched() -> None:
    diffs = _diffs()
    own = resolve_anchor(_finding(COMPUTE_NEW, 120), diffs, _no_files)
    assert own is not None and (own.match, own.own_file) == (Match.EXISTING_CODE, True)
    assert own.allows_suggestion()
    # The probe case: the cause sits in another file, so a suggestion would replace that file's code.
    caller = _finding("    wrap(payload)", 42, path="app/caller.py", cause_code="    send(payload, timeout=5)")
    probe = resolve_anchor(caller, diffs, _no_files)
    assert probe is not None and probe.anchor.path == "app/client.py"
    assert (probe.match, probe.own_file) == (Match.CAUSE_CODE, False) and not probe.allows_suggestion()
    # A cause in the finding's own file still anchors on other code than the suggestion replaces.
    same = resolve_anchor(_finding("        self.log(result)", 118, cause_code=COMPUTE_NEW), diffs, _no_files)
    assert same is not None and same.match is Match.CAUSE_CODE and not same.allows_suggestion()


def test_placement_line_fallback_allows_no_suggestion() -> None:
    placed = resolve_anchor(_finding("not in the code", 117), _diffs(), _no_files)
    assert placed == Placement(
        anchor=Anchor(path="app/service.py", line=117, side=Side.NEW, in_diff=True), match=Match.LINE, own_file=True
    )
    assert not placed.allows_suggestion()


def test_placement_existing_code_in_another_file_or_old_side_allows_no_suggestion() -> None:
    other = resolve_anchor(_finding("    send(payload, timeout=5)", 42, path="app/caller.py"), _diffs(), _no_files)
    assert other is not None and (other.match, other.own_file) == (Match.EXISTING_CODE, False)
    assert not other.allows_suggestion()
    old = resolve_anchor(_finding("def load(path):", 2, path="pkg/old_name.py"), _diffs("renamed.diff"), _no_files)
    assert old is not None and old.anchor.side is Side.OLD and old.own_file and not old.allows_suggestion()
