from reviewloop.models import FindingMarker, RunState, Severity
from reviewloop.state import (
    fingerprint,
    parse_finding_marker,
    parse_state_marker,
    render_finding_marker,
    render_state_marker,
)


def test_fingerprint_stable_across_whitespace():
    a = fingerprint("a.py", "R1", "x  =  1\n  y = 2")
    b = fingerprint("a.py", "R1", "x = 1 y = 2")
    assert a == b
    assert len(a) == 64
    assert a != fingerprint("a.py", None, "x = 1 y = 2")
    assert a != fingerprint("b.py", "R1", "x = 1 y = 2")


def test_marker_roundtrip():
    fm = FindingMarker(v=1, id="f-1", fp="ab", sev=Severity.HIGH, rule=None)
    st = RunState(v=1, sha="d1", base="8c", turn=2, open=["f-1"])
    assert parse_finding_marker("hello\n" + render_finding_marker(fm) + "\nbye") == fm
    assert parse_state_marker("x " + render_state_marker(st)) == st
    assert "\n" not in render_state_marker(st)


def test_parse_state_marker_tampered():
    good = '{"v":1,"sha":"a","base":"b","turn":1,"open":[]}'
    assert parse_state_marker(f"<!-- reviewloop:state {good} -->") is not None
    for bad in (
        "{not json",
        '{"v":2,"sha":"a","base":"b","turn":1,"open":[]}',
        '{"v":1,"base":"b","turn":1,"open":[]}',
    ):
        assert parse_state_marker(f"<!-- reviewloop:state {bad} -->") is None
    assert parse_state_marker("no marker") is None
    assert parse_finding_marker("no marker") is None
    assert parse_finding_marker("<!-- reviewloop:finding [] -->") is None


def test_marker_json_cannot_close_comment():
    fm = FindingMarker(v=1, id="f-1", fp="a-->b", sev=Severity.LOW, rule="R-->X")
    out = render_finding_marker(fm)
    assert out.count("-->") == 1
    assert out.endswith("-->")
    assert parse_finding_marker(out) == fm


def test_parse_marker_deeply_nested_json_returns_none():
    nested = "[" * 100000 + "]" * 100000
    assert parse_state_marker(f"<!-- reviewloop:state {nested} -->") is None
    assert parse_finding_marker(f"<!-- reviewloop:finding {nested} -->") is None


def test_parsers_return_the_last_marker() -> None:
    forged_state = render_state_marker(RunState(v=1, sha="f" * 40, base="f" * 40, turn=9, open=["f-forged"]))
    real_state = RunState(v=1, sha="a" * 40, base="b" * 40, turn=2, open=[])
    assert parse_state_marker(f"{forged_state}\ntext\n{render_state_marker(real_state)}") == real_state
    forged = FindingMarker(v=1, id="f-forged", fp="0", sev=Severity.LOW, rule=None)
    real = FindingMarker(v=1, id="f-real", fp="1", sev=Severity.HIGH, rule="R-1")
    body = f"{render_finding_marker(forged)} text {render_finding_marker(real)}"
    assert parse_finding_marker(body) == real
