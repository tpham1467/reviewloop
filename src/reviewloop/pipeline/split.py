"""Split a large diff into groups that one agent run each can review (spec 5.1, large-diff-review)."""

from reviewloop.diff import FileDiff, changed_lines

# A single file above this multiple of `max_lines` is more than one agent run should get; it is not grouped.
MAX_FILE_FACTOR = 4


def _top_dir(path: str) -> str:
    head, sep, _ = path.partition("/")
    return head if sep else ""


def _chunks(diffs: list[FileDiff], max_lines: int) -> list[list[FileDiff]]:
    """Each top-level directory's files, cut into runs of at most `max_lines`; a larger file is its own run."""
    by_dir: dict[str, list[FileDiff]] = {}
    for fd in diffs:
        by_dir.setdefault(_top_dir(fd.path), []).append(fd)
    chunks: list[list[FileDiff]] = []
    for files in by_dir.values():
        current: list[FileDiff] = []
        for fd in files:
            lines = changed_lines([fd])
            if lines > max_lines:
                chunks.append([fd])
            elif changed_lines([*current, fd]) > max_lines:
                chunks.append(current)
                current = [fd]
            else:
                current.append(fd)
        if current:
            chunks.append(current)
    return chunks


def plan_groups(diffs: list[FileDiff], max_lines: int = 3000, max_groups: int = 20) -> list[list[FileDiff]]:
    """Group files by top-level directory, then pack the directory runs first-fit into groups of `max_lines`.

    Files absent from the result were not grouped and the caller fails them as `too_large`: a single file above
    `max_lines * MAX_FILE_FACTOR`, and every file past the first `max_groups` groups.
    """
    fitting = [fd for fd in diffs if changed_lines([fd]) <= max_lines * MAX_FILE_FACTOR]
    groups: list[list[FileDiff]] = []
    for chunk in _chunks(fitting, max_lines):
        size = changed_lines(chunk)
        target = next((g for g in groups if changed_lines(g) + size <= max_lines), None)
        if target is None:
            groups.append(list(chunk))
        else:
            target.extend(chunk)
    return groups[:max_groups]
