from pathlib import Path

from reviewloop.diff import DiffLine, FileDiff, changed_new_lines, parse_unified_diff

FIXTURES = Path(__file__).parent.parent / "fixtures" / "diffs"


def _load(name: str) -> list[FileDiff]:
    return parse_unified_diff((FIXTURES / name).read_text(encoding="utf-8"))


def test_parse_added_file() -> None:
    [fd] = _load("added.diff")
    assert (fd.path, fd.old_path, fd.status, fd.is_binary, fd.mode_only) == (
        "src/new_mod.py",
        None,
        "added",
        False,
        False,
    )
    [hunk] = fd.hunks
    assert (hunk.old_start, hunk.new_start) == (0, 1)
    assert hunk.lines == [
        DiffLine(kind="add", old_no=None, new_no=1, text="def hello():"),
        DiffLine(kind="add", old_no=None, new_no=2, text='    return "hi"'),
        DiffLine(kind="add", old_no=None, new_no=3, text="# end"),
    ]
    assert changed_new_lines(fd) == {1, 2, 3}


def test_parse_deleted_file() -> None:
    [fd] = _load("deleted.diff")
    assert (fd.path, fd.old_path, fd.status) == ("old/gone.py", None, "deleted")
    assert [(ln.kind, ln.old_no, ln.new_no) for ln in fd.hunks[0].lines] == [("del", 1, None), ("del", 2, None)]
    assert changed_new_lines(fd) == set()


def test_parse_renamed_with_edits() -> None:
    [fd] = _load("renamed.diff")
    assert (fd.path, fd.old_path, fd.status) == ("pkg/new_name.py", "pkg/old_name.py", "renamed")
    assert [(ln.kind, ln.old_no, ln.new_no, ln.text) for ln in fd.hunks[0].lines] == [
        ("ctx", 1, 1, "import os"),
        ("del", 2, None, "def load(path):"),
        ("add", None, 2, 'def load(path, mode="r"):'),
        ("ctx", 3, 3, "    return open(path)"),
        ("ctx", 4, 4, "# tail"),
    ]
    assert changed_new_lines(fd) == {2}


def test_parse_pure_rename_without_hunks() -> None:
    text = "diff --git a/a.txt b/b.txt\nsimilarity index 100%\nrename from a.txt\nrename to b.txt\n"
    [fd] = parse_unified_diff(text)
    assert (fd.path, fd.old_path, fd.status, fd.hunks, fd.mode_only) == ("b.txt", "a.txt", "renamed", [], False)


def test_parse_binary_files() -> None:
    changed, added = _load("binary.diff")
    assert (changed.path, changed.status, changed.is_binary, changed.hunks) == (
        "assets/logo.png",
        "modified",
        True,
        [],
    )
    assert (added.path, added.status, added.is_binary) == ("assets/new icon.png", "added", True)


def test_parse_git_binary_patch() -> None:
    text = "diff --git a/b.bin b/b.bin\nindex 1111111..2222222 100644\nGIT binary patch\nliteral 3\nKcmZ?wW&i>J00II4\n"
    [fd] = parse_unified_diff(text)
    assert fd.is_binary and fd.status == "modified" and fd.hunks == []


def test_parse_mode_only() -> None:
    [fd] = _load("mode_only.diff")
    assert (fd.path, fd.status, fd.mode_only, fd.is_binary, fd.hunks) == (
        "scripts/run.sh",
        "modified",
        True,
        False,
        [],
    )


def test_parse_mode_change_with_edits_is_not_mode_only() -> None:
    text = (
        "diff --git a/r.sh b/r.sh\n"
        "old mode 100644\n"
        "new mode 100755\n"
        "--- a/r.sh\n"
        "+++ b/r.sh\n"
        "@@ -1 +1 @@\n"
        "-echo a\n"
        "+echo b\n"
    )
    [fd] = parse_unified_diff(text)
    assert not fd.mode_only and changed_new_lines(fd) == {1}


def test_parse_no_newline_marker() -> None:
    [fd] = _load("no_newline.diff")
    assert [(ln.kind, ln.old_no, ln.new_no, ln.text) for ln in fd.hunks[0].lines] == [
        ("ctx", 1, 1, "alpha"),
        ("del", 2, None, "beta"),
        ("add", None, 2, "beta"),
        ("add", None, 3, "gamma"),
    ]


def test_parse_crlf_content_and_crlf_diff() -> None:
    text = "diff --git a/w.txt b/w.txt\r\n--- a/w.txt\r\n+++ b/w.txt\r\n@@ -1,2 +1,2 @@\r\n one\r\n-two\r\n+TWO\r\n"
    [fd] = parse_unified_diff(text)
    assert fd.path == "w.txt"
    assert [ln.text for ln in fd.hunks[0].lines] == ["one", "two", "TWO"]


def test_parse_multiple_hunks_and_files() -> None:
    service, client, util = _load("anchor.diff")
    assert [h.new_start for h in service.hunks] == [115, 138]
    assert changed_new_lines(service) == {117, 118, 140}
    assert service.hunks[1].lines[-1] == DiffLine(kind="ctx", old_no=140, new_no=142, text="        return None")
    assert (client.path, util.path) == ("app/client.py", "app/util.py")


def test_parse_hunk_line_that_looks_like_header() -> None:
    text = "diff --git a/m.md b/m.md\n--- a/m.md\n+++ b/m.md\n@@ -1,2 +1,2 @@\n--- old rule\n+++ new rule\n keep\n"
    [fd] = parse_unified_diff(text)
    assert [(ln.kind, ln.text) for ln in fd.hunks[0].lines] == [
        ("del", "-- old rule"),
        ("add", "++ new rule"),
        ("ctx", "keep"),
    ]


def test_parse_empty_context_line_with_stripped_space() -> None:
    text = "diff --git a/e.py b/e.py\n--- a/e.py\n+++ b/e.py\n@@ -1,3 +1,3 @@\n a\n\n-b\n+c\n"
    [fd] = parse_unified_diff(text)
    assert [(ln.kind, ln.old_no, ln.new_no, ln.text) for ln in fd.hunks[0].lines] == [
        ("ctx", 1, 1, "a"),
        ("ctx", 2, 2, ""),
        ("del", 3, None, "b"),
        ("add", None, 3, "c"),
    ]


def test_parse_quoted_paths() -> None:
    text = 'diff --git "a/docs/caf\\303\\251 \\"x\\".md" "b/docs/caf\\303\\251 \\"x\\".md"\nnew file mode 100644\n'
    [fd] = parse_unified_diff(text)
    assert (fd.path, fd.status, fd.hunks) == ('docs/café "x".md', "added", [])


def test_parse_plain_unified_diff_without_git_header() -> None:
    text = (
        "--- a/one.txt\t2026-01-01 00:00:00\n"
        "+++ b/one.txt\t2026-01-01 00:00:01\n"
        "@@ -1 +1 @@\n"
        "-a\n"
        "+b\n"
        "--- a/two.txt\n"
        "+++ b/two.txt\n"
        "@@ -1 +1,2 @@\n"
        " x\n"
        "+y\n"
    )
    one, two = parse_unified_diff(text)
    assert (one.path, one.status, changed_new_lines(one)) == ("one.txt", "modified", {1})
    assert (two.path, changed_new_lines(two)) == ("two.txt", {2})


def test_parse_empty_text() -> None:
    assert parse_unified_diff("") == []


def test_parse_truncated_hunk_resyncs_on_next_file() -> None:
    text = (
        "diff --git a/a.py b/a.py\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -1,5 +1,5 @@\n"
        "-x\n"
        "+y\n"
        "diff --git a/b.py b/b.py\n"
        "--- a/b.py\n"
        "+++ b/b.py\n"
        "@@ -1 +1 @@\n"
        "-p\n"
        "+q\n"
    )
    a, b = parse_unified_diff(text)
    assert (a.path, changed_new_lines(a), b.path, changed_new_lines(b)) == ("a.py", {1}, "b.py", {1})


def test_parse_truncated_last_hunk_has_no_phantom_line() -> None:
    # The header promises 5 lines but the text ends after 2; the trailing newline is not a context line.
    text = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,5 +1,5 @@\n-x\n+y\n"
    [fd] = parse_unified_diff(text)
    assert [(ln.kind, ln.text) for ln in fd.hunks[0].lines] == [("del", "x"), ("add", "y")]


def test_parse_ignores_preamble_before_first_file() -> None:
    text = (
        "From 1234 Mon Sep 17 00:00:00 2001\nSubject: change\n\n"
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x\n+y\n"
    )
    [fd] = parse_unified_diff(text)
    assert (fd.path, changed_new_lines(fd)) == ("a.py", {1})


def test_changed_lines_counts_adds_and_deletes() -> None:
    from reviewloop.diff import changed_lines

    text = "--- a/x\n+++ b/x\n@@ -1,2 +1,2 @@\n a\n-b\n+c\n--- a/y\n+++ b/y\n@@ -0,0 +1 @@\n+d\n"
    assert changed_lines(parse_unified_diff(text)) == 3
