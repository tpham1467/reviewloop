"""Split a large diff into groups that one agent run each can review (spec 5.1, large-diff-review)."""

from reviewloop.diff import FileDiff, changed_lines

# A single file above this multiple of `max_lines` is more than one agent run should get; it is not grouped.
MAX_FILE_FACTOR = 4


def _top_dir(path: str) -> str:
    head, sep, _ = path.partition("/")
    return head if sep else ""


Chunk = tuple[list[FileDiff], int]


def _chunks(sized: list[tuple[FileDiff, int]], max_lines: int) -> list[Chunk]:
    """Each top-level directory's files, cut into runs of at most `max_lines`; a larger file is its own run."""
    by_dir: dict[str, list[tuple[FileDiff, int]]] = {}
    for fd, lines in sized:
        by_dir.setdefault(_top_dir(fd.path), []).append((fd, lines))
    chunks: list[Chunk] = []
    for files in by_dir.values():
        current: list[FileDiff] = []
        size = 0
        for fd, lines in files:
            if lines > max_lines:
                chunks.append(([fd], lines))
                continue
            if size + lines > max_lines:
                chunks.append((current, size))
                current, size = [], 0
            current.append(fd)
            size += lines
        if current:
            chunks.append((current, size))
    return chunks


def plan_groups(diffs: list[FileDiff], max_lines: int = 3000, max_groups: int = 20) -> list[list[FileDiff]]:
    """Group files by top-level directory, then pack the directory runs first-fit into groups of `max_lines`.

    Files absent from the result were not grouped and the caller fails them as `too_large`: a single file above
    `max_lines * MAX_FILE_FACTOR`, and every file past the first `max_groups` groups.
    """
    sized = [(fd, changed_lines([fd])) for fd in diffs]
    fitting = [(fd, lines) for fd, lines in sized if lines <= max_lines * MAX_FILE_FACTOR]
    groups: list[list[FileDiff]] = []
    sizes: list[int] = []
    for files, size in _chunks(fitting, max_lines):
        target = next((i for i, used in enumerate(sizes) if used + size <= max_lines), None)
        if target is None:
            groups.append(list(files))
            sizes.append(size)
        else:
            groups[target].extend(files)
            sizes[target] += size
    return groups[:max_groups]
