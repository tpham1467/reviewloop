"""How a failure reads in the summary and in a failed file's coverage reason."""

from reviewloop.errors import ReviewloopError
from reviewloop.output.redact import redact

MAX_REASON = 200


def error_text(exc: BaseException) -> str:
    # Messages of unexpected exceptions may carry internals; ReviewloopError messages are written for users.
    return str(exc) if isinstance(exc, ReviewloopError) else f"internal error ({type(exc).__name__})"


def failure_reason(where: str, exc: BaseException) -> str:
    """The one-line coverage reason for files that failed because `where` raised `exc`.

    Redaction runs on the full text first: a cut could split a token or key block so no pattern matches it.
    """
    return f"{where}: {' '.join(redact(error_text(exc)).split())[:MAX_REASON]}"
