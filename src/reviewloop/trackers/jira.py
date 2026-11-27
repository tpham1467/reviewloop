from typing import Any
from urllib.parse import urlparse

from atlassian import Jira  # pyright: ignore[reportMissingTypeStubs]
from requests import HTTPError

from reviewloop.errors import ReviewloopError
from reviewloop.trackers._text import truncate
from reviewloop.trackers.base import Ticket


def is_cloud(url: str) -> bool:
    return (urlparse(url).hostname or "").endswith(".atlassian.net")


def _adf_to_text(node: Any) -> str:
    """Flatten an Atlassian Document Format tree into plain text."""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "\n".join(_adf_to_text(child) for child in node)  # pyright: ignore[reportUnknownVariableType]
    if isinstance(node, dict):
        if node.get("type") == "text":  # pyright: ignore[reportUnknownMemberType]
            return str(node.get("text", ""))  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
        return _adf_to_text(node.get("content", []))  # pyright: ignore[reportUnknownMemberType]
    return ""


class JiraTracker:
    def __init__(self, client: Any, base_url: str) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")

    @classmethod
    def from_credentials(cls, url: str, token: str, email: str | None) -> "JiraTracker":
        if email is not None:
            client = Jira(url=url, username=email, password=token, cloud=True)
        else:
            client = Jira(url=url, token=token)
        return cls(client, url)

    def fetch(self, keys: list[str]) -> list[Ticket]:
        tickets: list[Ticket] = []
        for key in keys:
            try:
                issue = self._client.get_issue(key)
            except HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else None
                if status in (403, 404):
                    continue
                raise ReviewloopError(f"Jira issue {key} fetch failed with HTTP {status}") from exc
            fields = issue.get("fields", {})
            tickets.append(
                Ticket(
                    key=key,
                    title=str(fields.get("summary", "")),
                    body=truncate(_adf_to_text(fields.get("description"))),
                    url=f"{self._base_url}/browse/{key}",
                )
            )
        return tickets
