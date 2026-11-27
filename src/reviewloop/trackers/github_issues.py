from typing import Any

from github import Auth, Github, GithubException

from reviewloop.errors import ReviewloopError
from reviewloop.trackers._text import truncate
from reviewloop.trackers.base import Ticket


class GithubIssuesTracker:
    def __init__(self, repo: Any) -> None:
        self._repo = repo

    @classmethod
    def from_token(cls, token: str, repository: str) -> "GithubIssuesTracker":
        return cls(Github(auth=Auth.Token(token)).get_repo(repository, lazy=True))

    def fetch(self, keys: list[str]) -> list[Ticket]:
        tickets: list[Ticket] = []
        for key in keys:
            number = key.lstrip("#")
            if not number.isdigit():
                continue
            try:
                issue = self._repo.get_issue(int(number))
            except GithubException as exc:
                if exc.status in (403, 404):
                    continue
                raise ReviewloopError(f"GitHub issue {key} fetch failed with HTTP {exc.status}") from exc
            if issue.pull_request is not None:
                continue
            tickets.append(Ticket(key=f"#{number}", title=issue.title, body=truncate(issue.body), url=issue.html_url))
        return tickets
