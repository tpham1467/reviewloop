import pytest
from score import Expected, Metrics, aggregate, from_counts, regressions, score

from reviewloop.models import Finding


def got(path: str = "a.py", line: int = 10, category: str = "null-safety") -> Finding:
    return Finding(path=path, line=line, severity="high", category=category, text="t", existing_code="x")  # type: ignore[arg-type]


def exp(path: str = "a.py", line: int = 10, category: str = "null-safety") -> Expected:
    return Expected(path=path, line=line, category=category)


def test_match_same_file_line_window_category() -> None:
    assert score([exp()], [got(line=13)]).tp == 1
    assert score([exp()], [got(line=7)]).tp == 1
    assert score([exp()], [got(line=14)]).tp == 0
    assert score([exp()], [got(path="b.py")]).tp == 0
    assert score([exp()], [got(category="security")]).tp == 0
    assert score([exp()], [got(line=14)], line_window=4).tp == 1


def test_one_expected_matches_once() -> None:
    m = score([exp()], [got(line=10), got(line=11)])
    assert (m.tp, m.fp, m.fn) == (1, 1, 0)
    assert m.precision == 0.5
    assert m.recall == 1.0


def test_maximum_matching_beats_greedy_nearest_first() -> None:
    # Greedy pairs the finding at 12 with the bug at 10 (distance 2 < 3) and strands the one at 8.
    m = score([exp(line=10), exp(line=15)], [got(line=12), got(line=8)])
    assert (m.tp, m.fp, m.fn) == (2, 0, 0)


def test_multi_line_range_matches_inside_the_window() -> None:
    assert score([exp(line=20)], [got(line=30)], starts=[24]).tp == 0
    assert score([exp(line=20)], [got(line=30)], starts=[23]).tp == 1
    assert score([exp(line=20)], [got(line=30)], starts=[None]).tp == 0
    assert score([exp(line=20)], [got(line=25)], starts=[15]).tp == 1


def test_closest_expected_wins() -> None:
    m = score([exp(line=10), exp(line=14)], [got(line=13)])
    assert (m.tp, m.fp, m.fn) == (1, 0, 1)
    m = score([exp(line=10), exp(line=14)], [got(line=13), got(line=10)])
    assert (m.tp, m.fp, m.fn) == (2, 0, 0)


def test_metrics_zero_division() -> None:
    assert score([], []) == Metrics(precision=0.0, recall=0.0, tp=0, fp=0, fn=0)
    assert score([exp()], []).recall == 0.0
    assert score([exp()], []).precision == 0.0
    only_fp = score([], [got()])
    assert (only_fp.precision, only_fp.recall, only_fp.fp) == (0.0, 0.0, 1)


def test_aggregate_sums_counts_not_ratios() -> None:
    total = aggregate([from_counts(1, 0, 0), from_counts(1, 3, 1)])
    assert (total.tp, total.fp, total.fn) == (2, 3, 1)
    assert total.precision == pytest.approx(0.4)
    assert total.recall == pytest.approx(2 / 3)
    assert aggregate([]) == from_counts(0, 0, 0)


def test_regressions_report_drops_beyond_margin() -> None:
    base = Metrics(precision=0.8, recall=0.7, tp=0, fp=0, fn=0)
    assert regressions(base, Metrics(precision=0.76, recall=0.66, tp=0, fp=0, fn=0)) == []
    assert regressions(base, Metrics(precision=0.9, recall=0.7, tp=0, fp=0, fn=0)) == []
    bad = regressions(base, Metrics(precision=0.7, recall=0.6, tp=0, fp=0, fn=0))
    assert len(bad) == 2
    assert "precision" in bad[0] and "recall" in bad[1]
    assert len(regressions(base, Metrics(precision=0.7, recall=0.7, tp=0, fp=0, fn=0), margin=0.2)) == 0


def test_below_floor_flags_each_metric_under_the_spec_floor() -> None:
    from score import MIN_PRECISION, MIN_RECALL, below_floor

    assert (MIN_RECALL, MIN_PRECISION) == (0.6, 0.5)
    assert below_floor(Metrics(precision=0.5, recall=0.6, tp=0, fp=0, fn=0)) == []
    low_p = below_floor(Metrics(precision=0.49, recall=0.9, tp=0, fp=0, fn=0))
    assert len(low_p) == 1 and "precision" in low_p[0]
    low_r = below_floor(Metrics(precision=0.9, recall=0.59, tp=0, fp=0, fn=0))
    assert len(low_r) == 1 and "recall" in low_r[0]
    assert len(below_floor(Metrics(precision=0.0, recall=0.0, tp=0, fp=0, fn=0))) == 2
