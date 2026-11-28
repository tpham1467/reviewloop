"""Text conventions every VCS adapter shares: the command syntax, the summary marker and the suggestion fence."""

import re

from reviewloop.models import Command

SUMMARY_MARKER = "<!-- reviewloop:summary -->"
COMMAND_PREFIX = "/reviewloop"


def parse_command(body: str) -> Command | None:
    """The command on the first non-empty line, exactly `/reviewloop <review|recheck|help>` (spec 6.1)."""
    first = next((line.strip() for line in body.splitlines() if line.strip()), "")
    parts = first.split()
    if len(parts) != 2 or parts[0] != COMMAND_PREFIX:
        return None
    try:
        return Command(parts[1])
    except ValueError:
        return None


def suggestion_fence(code: str) -> str:
    """A backtick fence longer than any backtick run in `code`, so the code cannot close it."""
    longest = max((len(run) for run in re.findall(r"`+", code)), default=0)
    return "`" * max(3, longest + 1)
