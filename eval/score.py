"""Deterministic scorer: a finding matches ground truth by file, line window and category (spec 9). No LLM judge."""

from dataclasses import dataclass

from pydantic import BaseModel

from reviewloop.models import Finding

DEFAULT_WINDOW = 3
DEFAULT_MARGIN = 0.05
# Spec section 9: the seeded set must reach this floor for the first release, with or without a baseline.
MIN_RECALL = 0.6
MIN_PRECISION = 0.5


class Expected(BaseModel):
    path: str
    line: int
    category: str


@dataclass(frozen=True)
class Metrics:
    precision: float
    recall: float
    tp: int
    fp: int
    fn: int


def from_counts(tp: int, fp: int, fn: int) -> Metrics:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return Metrics(precision=precision, recall=recall, tp=tp, fp=fp, fn=fn)


def _distance(e: Expected, f: Finding, start: int | None) -> int:
    """Lines between the expected line and the finding's span (`start..line`, or just `line`); 0 inside the span."""
    first = f.line if start is None else min(start, f.line)
    return max(first - e.line, e.line - f.line, 0)


def score(
    expected: list[Expected],
    got: list[Finding],
    line_window: int = DEFAULT_WINDOW,
    starts: list[int | None] | None = None,
) -> Metrics:
    """Maximum matching between findings and expected bugs: same file and category, span within `line_window`.

    `starts[i]` is the first line of finding i's multi-line range, if any. Each expected bug and each finding pairs
    at most once. Among maximum matchings the search tries nearer candidates first, so ties favour closer lines.
    """
    candidates: list[list[int]] = []
    for gi, f in enumerate(got):
        start = starts[gi] if starts is not None else None
        near = [
            (_distance(e, f, start), ei)
            for ei, e in enumerate(expected)
            if f.path == e.path and f.category == e.category
        ]
        candidates.append([ei for d, ei in sorted(near) if d <= line_window])
    owner: dict[int, int] = {}  # expected index -> finding index

    def augment(gi: int, seen: set[int]) -> bool:
        for ei in candidates[gi]:
            if ei in seen:
                continue
            seen.add(ei)
            if ei not in owner or augment(owner[ei], seen):
                owner[ei] = gi
                return True
        return False

    tp = sum(augment(gi, set()) for gi in range(len(got)))
    return from_counts(tp, len(got) - tp, len(expected) - tp)


def aggregate(parts: list[Metrics]) -> Metrics:
    """Micro average: sum the counts, then compute the ratios."""
    return from_counts(sum(m.tp for m in parts), sum(m.fp for m in parts), sum(m.fn for m in parts))


def regressions(baseline: Metrics, current: Metrics, margin: float = DEFAULT_MARGIN) -> list[str]:
    """One line per metric that dropped more than `margin` below the baseline."""
    out: list[str] = []
    for name in ("precision", "recall"):
        before: float = getattr(baseline, name)
        after: float = getattr(current, name)
        if before - after > margin:
            out.append(f"{name} fell from {before:.3f} to {after:.3f} (allowed drop {margin})")
    return out


def below_floor(current: Metrics) -> list[str]:
    """One line per metric under the spec 9 floor; checked on every run, apart from the regression check."""
    out: list[str] = []
    if current.precision < MIN_PRECISION:
        out.append(f"precision {current.precision:.3f} is below the floor {MIN_PRECISION}")
    if current.recall < MIN_RECALL:
        out.append(f"recall {current.recall:.3f} is below the floor {MIN_RECALL}")
    return out
