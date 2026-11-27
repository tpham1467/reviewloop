import json
import re
from pathlib import Path
from typing import Any, cast

import httpx
from github import Auth, Github, GithubException

from reviewloop.errors import ReviewloopError
from reviewloop.models import (
    Anchor,
    Command,
    Event,
    EventKind,
    Finding,
    PullRequest,
    Reply,
    Side,
    Thread,
    ThreadRef,
    User,
)
from reviewloop.state import parse_finding_marker, parse_state_marker

SUMMARY_MARKER = "<!-- reviewloop:summary -->"
_GRAPHQL_URL = "https://api.github.com/graphql"
_ACTIONS_BOT = User(id="41898282", username="github-actions[bot]")
_OPEN_ACTIONS = {"opened", "reopened", "ready_for_review"}
_MAX_SUGGESTION_LINES = 30
_WRITE_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}
_WRITE_PERMISSIONS = {"admin", "maintain", "write"}

_THREADS_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes { id isResolved comments(first: 1) { nodes { databaseId } } }
      }
    }
  }
}
"""
_RESOLVE_MUTATION = """
mutation($id: ID!) {
  resolveReviewThread(input: {threadId: $id}) { thread { id isResolved } }
}
"""


class GitHubAdapter:
    """VCS adapter for GitHub Actions.

    Environment mapping: ``GITHUB_TOKEN`` -> ``token``, ``GITHUB_REPOSITORY`` -> ``repo``,
    ``GITHUB_EVENT_PATH`` -> ``event_path``, ``GITHUB_EVENT_NAME`` -> ``event_name``.
    """

    def __init__(self, token: str, repo: str, event_path: Path, event_name: str) -> None:
        self._token = token
        self._repo_name = repo
        self._event_path = event_path
        self._event_name = event_name
        self._gh = Github(auth=Auth.Token(token))
        self._repo = self._gh.get_repo(repo)
        self._payload: dict[str, Any] | None = None
        self._bot: User | None = None
        self._pr: Any = None

    def _load_payload(self) -> dict[str, Any]:
        if self._payload is None:
            try:
                raw: Any = json.loads(self._event_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise ReviewloopError(f"cannot read event payload: {exc}") from exc
            if not isinstance(raw, dict):
                raise ReviewloopError("event payload is not a JSON object")
            self._payload = cast("dict[str, Any]", raw)
        return self._payload

    def event(self) -> Event:
        payload = self._load_payload()
        action = payload.get("action")
        if self._event_name == "pull_request":
            pr: dict[str, Any] = payload.get("pull_request") or {}
            if action not in _OPEN_ACTIONS or not pr or pr.get("draft"):
                return Event(kind=EventKind.IGNORED)
            return Event(
                kind=EventKind.OPENED,
                actor=self._user(pr.get("user")),
                pr_number=pr.get("number", payload.get("number")),
            )
        if self._event_name == "issue_comment":
            issue: dict[str, Any] = payload.get("issue") or {}
            comment: dict[str, Any] = payload.get("comment") or {}
            if action != "created" or "pull_request" not in issue:
                return Event(kind=EventKind.IGNORED)
            command = self._parse_command(comment.get("body") or "")
            if command is None:
                return Event(kind=EventKind.IGNORED)
            return Event(
                kind=EventKind.COMMAND,
                command=command,
                actor=self._user(comment.get("user")),
                pr_number=issue.get("number"),
            )
        return Event(kind=EventKind.IGNORED)

    @staticmethod
    def _parse_command(body: str) -> Command | None:
        lines = [line.strip() for line in body.splitlines() if line.strip()]
        if not lines:
            return None
        parts = lines[0].split()
        if len(parts) != 2 or parts[0] != "/reviewloop":
            return None
        try:
            return Command(parts[1])
        except ValueError:
            return None

    @staticmethod
    def _user(raw: Any) -> User | None:
        if not raw:
            return None
        return User(id=str(raw["id"]), username=raw["login"])

    def _pr_number(self) -> int:
        number = self.event().pr_number
        if number is None:
            raise ReviewloopError("event has no pull request number")
        return number

    def _pull(self) -> Any:
        if self._pr is None:
            self._pr = self._repo.get_pull(self._pr_number())
        return self._pr

    def pull_request(self) -> PullRequest:
        pr = self._pull()
        head_repo = pr.head.repo
        is_fork = head_repo is None or head_repo.full_name != pr.base.repo.full_name
        return PullRequest(
            number=pr.number,
            title=pr.title,
            body=pr.body or "",
            source_branch=pr.head.ref,
            target_branch=pr.base.ref,
            head_sha=pr.head.sha,
            base_sha=pr.base.sha,
            author=User(id=str(pr.user.id), username=pr.user.login),
            is_fork=is_fork,
            web_url=pr.html_url,
        )

    def bot_identity(self) -> User:
        if self._bot is None:
            try:
                me = self._gh.get_user()
                self._bot = User(id=str(me.id), username=me.login)
            except GithubException as exc:
                # The Actions GITHUB_TOKEN cannot call /user (403).
                if exc.status != 403:
                    raise ReviewloopError(f"cannot resolve bot identity: HTTP {exc.status}") from exc
                self._bot = _ACTIONS_BOT
        return self._bot

    def can_write(self, user: User) -> bool:
        comment: dict[str, Any] = self._load_payload().get("comment") or {}
        commenter: dict[str, Any] = comment.get("user") or {}
        author = commenter.get("login")
        if self._event_name == "issue_comment" and author == user.username:
            return comment.get("author_association") in _WRITE_ASSOCIATIONS
        try:
            permission = self._repo.get_collaborator_permission(user.username)
        except GithubException as exc:
            if exc.status == 404:
                return False
            raise ReviewloopError(f"cannot read permission: HTTP {exc.status}") from exc
        return permission in _WRITE_PERMISSIONS

    def _graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        try:
            response = httpx.post(
                _GRAPHQL_URL,
                json={"query": query, "variables": variables},
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=30,
            )
            response.raise_for_status()
            body: Any = response.json()
        except httpx.HTTPStatusError as exc:
            raise ReviewloopError(f"GitHub GraphQL request failed: HTTP {exc.response.status_code}") from None
        except (httpx.HTTPError, ValueError) as exc:
            raise ReviewloopError(f"GitHub GraphQL request failed: {type(exc).__name__}") from None
        if not isinstance(body, dict):
            raise ReviewloopError("GitHub GraphQL returned a non-object response")
        payload = cast("dict[str, Any]", body)
        if payload.get("errors") or payload.get("data") is None:
            raise ReviewloopError("GitHub GraphQL returned errors or no data")
        return cast("dict[str, Any]", payload["data"])

    def _thread_states(self) -> dict[str, tuple[str, bool]]:
        """Map root review comment id to (GraphQL thread id, resolved)."""
        owner, name = self._repo_name.split("/", 1)
        states: dict[str, tuple[str, bool]] = {}
        cursor: str | None = None
        while True:
            data = self._graphql(
                _THREADS_QUERY,
                {"owner": owner, "name": name, "number": self._pr_number(), "cursor": cursor},
            )
            threads = data["repository"]["pullRequest"]["reviewThreads"]
            for node in threads["nodes"]:
                comments = node["comments"]["nodes"]
                if comments:
                    states[str(comments[0]["databaseId"])] = (node["id"], node["isResolved"])
            if not threads["pageInfo"]["hasNextPage"]:
                return states
            cursor = threads["pageInfo"]["endCursor"]

    def threads(self) -> list[Thread]:
        bot = self.bot_identity().username
        comments = list(self._pull().get_review_comments())
        replies: dict[int, list[Any]] = {}
        for comment in comments:
            if comment.in_reply_to_id is not None:
                replies.setdefault(comment.in_reply_to_id, []).append(comment)
        states = self._thread_states()
        result: list[Thread] = []
        for root in comments:
            if root.in_reply_to_id is not None or root.user.login != bot:
                continue
            node_id, resolved = states.get(str(root.id), (str(root.id), False))
            result.append(
                Thread(
                    ref=ThreadRef(id=node_id, note_id=str(root.id)),
                    path=root.path,
                    line=root.line,
                    body=root.body,
                    resolved=resolved,
                    replies=[
                        Reply(
                            author=User(id=str(r.user.id), username=r.user.login),
                            body=r.body,
                            created_at=r.created_at,
                        )
                        for r in replies.get(root.id, [])
                    ],
                    marker=parse_finding_marker(root.body),
                )
            )
        return result

    def post_inline(self, finding: Finding, anchor: Anchor) -> ThreadRef:
        pr = self._pull()
        body = finding.text
        kwargs: dict[str, Any] = {"commit": pr.head.sha, "path": anchor.path}
        if anchor.in_diff:
            side = "LEFT" if anchor.side == Side.OLD else "RIGHT"
            if finding.suggestion is not None and not finding.security:
                span = anchor.line - (anchor.start_line or anchor.line) + 1
                block = self.suggestion_block(anchor, finding.suggestion, span)
                if block is not None:
                    body = f"{body}\n\n{block}"
            kwargs.update(line=anchor.line, side=side)
            if anchor.start_line is not None:
                kwargs.update(start_line=anchor.start_line, start_side=side)
        else:
            kwargs["subject_type"] = "file"
        try:
            comment = pr.create_review_comment(body=body, **kwargs)
        except GithubException as exc:
            if exc.status != 422:
                raise
            # GitHub rejects positions it cannot place (for example a range crossing hunks).
            where = f"{anchor.start_line}-{anchor.line}" if anchor.start_line is not None else str(anchor.line)
            # A general comment cannot apply a suggestion block, so use the bare text.
            return self.post_general(f"`{anchor.path}:{where}`\n\n{finding.text}")
        return ThreadRef(id=str(comment.id), note_id=str(comment.id))

    def post_general(self, body: str) -> ThreadRef:
        comment = self._pull().create_issue_comment(body)
        return ThreadRef(id=str(comment.id))

    def reply(self, thread: ThreadRef, body: str) -> None:
        if thread.note_id is None:
            raise ReviewloopError("thread has no root comment id to reply to")
        self._pull().create_review_comment_reply(int(thread.note_id), body)

    def resolve(self, thread: ThreadRef) -> None:
        node_id = thread.id
        if not node_id.startswith("PRRT_"):
            found = self._thread_states().get(thread.note_id or thread.id)
            if found is None:
                raise ReviewloopError(f"review thread {thread.id} not found")
            node_id = found[0]
        self._graphql(_RESOLVE_MUTATION, {"id": node_id})

    def upsert_summary(self, body: str) -> None:
        pr = self._pull()
        bot = self.bot_identity().username
        if SUMMARY_MARKER not in body and parse_state_marker(body) is None:
            body = f"{body}\n{SUMMARY_MARKER}"
        for comment in pr.get_issue_comments():
            if comment.user.login == bot and (
                SUMMARY_MARKER in comment.body or parse_state_marker(comment.body) is not None
            ):
                comment.edit(body)
                return
        pr.create_issue_comment(body)

    def suggestion_block(self, anchor: Anchor, code: str, replaced_lines: int = 1) -> str | None:
        if anchor.side == Side.OLD or not anchor.in_diff or replaced_lines > _MAX_SUGGESTION_LINES:
            return None
        longest = max((len(run) for run in re.findall(r"`+", code)), default=0)
        fence = "`" * max(3, longest + 1)
        return f"{fence}suggestion\n{code}\n{fence}"

    def read_file(self, path: str, ref: str) -> str | None:
        try:
            content = self._repo.get_contents(path, ref=ref)
        except GithubException:
            return None
        if isinstance(content, list):
            return None
        return content.decoded_content.decode("utf-8", errors="replace")
