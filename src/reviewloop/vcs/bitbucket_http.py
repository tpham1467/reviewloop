"""HTTP error handling shared by the Bitbucket Cloud and Data Center adapters (both use atlassian-python-api)."""

from reviewloop.errors import ReviewloopError


def http_status(exc: BaseException | None) -> int | None:
    """The HTTP status of a requests error, or None for a network error or any other exception."""
    response = getattr(exc, "response", None)
    return getattr(response, "status_code", None)


def failure(what: str, exc: BaseException) -> ReviewloopError:
    """A token-free error naming the call and its HTTP status; the response body may echo request data."""
    status = http_status(exc)
    detail = f"HTTP {status}" if status is not None else "network error"
    return ReviewloopError(f"Bitbucket {what} failed ({detail})")
