from typing import Any

import gitlab
from gitlab.exceptions import GitlabError

from reviewloop.errors import ReviewloopError
from reviewloop.trackers._text import truncate
from reviewloop.trackers.base import Ticket


class GitlabIssuesTracker:
    def __init__(self, project: Any) -> None:
        self._project = project

    @classmethod
    def from_token(cls, server_url: str, project_id: str, token: str) -> "GitlabIssuesTracker":
        client = gitlab.Gitlab(server_url, private_token=token)
        return cls(client.projects.get(project_id, lazy=True))

    def fetch(self, keys: list[str]) -> list[Ticket]:
        tickets: list[Ticket] = []
        for key in keys:
            number = key.lstrip("#")
            if not number.isdigit():
                continue
            try:
                issue = self._project.issues.get(int(number))
            except GitlabError as exc:
                status = getattr(exc, "response_code", None)
                if status in (403, 404):
                    continue
                raise ReviewloopError(f"GitLab issue {key} fetch failed with HTTP {status}") from exc
            tickets.append(
                Ticket(key=f"#{number}", title=issue.title, body=truncate(issue.description), url=issue.web_url)
            )
        return tickets
