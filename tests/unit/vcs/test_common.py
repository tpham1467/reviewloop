import pytest

from reviewloop.models import Command
from reviewloop.vcs.common import parse_command, suggestion_fence


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("/reviewloop review", Command.REVIEW),
        ("\n  /reviewloop recheck  \nthanks", Command.RECHECK),
        ("/reviewloop help", Command.HELP),
    ],
)
def test_parse_command_first_non_empty_line(body: str, expected: Command) -> None:
    assert parse_command(body) is expected


@pytest.mark.parametrize(
    "body",
    [
        "",
        "   \n  ",
        "/reviewloop",
        "/reviewloop nope",
        "/reviewloop review now",
        "/Reviewloop review",
        "/reviewloop Review",
        "please run\n/reviewloop review",
        "\\/reviewloop review",
    ],
)
def test_parse_command_rejects_everything_else(body: str) -> None:
    assert parse_command(body) is None


@pytest.mark.parametrize(("code", "fence"), [("x = 1", "```"), ("a ``` b", "````"), ("`````", "``````")])
def test_suggestion_fence_outgrows_backtick_runs(code: str, fence: str) -> None:
    assert suggestion_fence(code) == fence
