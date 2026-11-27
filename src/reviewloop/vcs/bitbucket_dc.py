"""Bitbucket Data Center adapter.

DC anchors carry a single line, so `Anchor.start_line` is ignored for inline comments.
"""

import re
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from typing import Any, cast
from urllib.parse import quote

from atlassian import Bitbucket
from requests import RequestException

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
_NOT_FOUND = 404
_REJECTED_ANCHOR = frozenset({400, 404, 409})
_NO_RESOLVE = frozenset({400, 405})
_REQUIRED_ENV = ("BB_URL", "REVIEWLOOP_BITBUCKET_TOKEN", "BB_PROJECT", "BB_REPO")
_PAGE = 100
_WRITE_PERMISSIONS = frozenset({"REPO_WRITE", "REPO_ADMIN"})
_PROJECT_WRITE_PERMISSIONS = frozenset({"PROJECT_WRITE", "PROJECT_ADMIN"})
_FOUR_BYTE = re.compile("[\U00010000-\U0010ffff]")


def clean_text(text: str) -> str:
    """Bitbucket DC answers 500 to 4-byte characters (emoji and the like)."""
    return _FOUR_BYTE.sub("�", text)


def _status(exc: BaseException | None) -> int | None:
    response = exc.response if isinstance(exc, RequestException) else None
    return response.status_code if response is not None else None


def _failure(what: str, exc: RequestException) -> ReviewloopError:
    status = _status(exc)
    detail = f"HTTP {status}" if status is not None else "network error"
    return ReviewloopError(f"Bitbucket {what} failed ({detail})")


def _user(raw: Mapping[str, Any]) -> User:
    slug = str(raw["slug"])
    return User(id=slug, username=slug)


class BitbucketDCAdapter:
    def __init__(
        self,
        url: str,
        token: str,
        project: str,
        repo: str,
        env: Mapping[str, str],
        *,
        client: Any = None,
    ) -> None:
        self._env = env
        self._client: Any = client or Bitbucket(url=url, token=token)
        self._project = project
        self._repo = repo
        self._bot: User | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "BitbucketDCAdapter":
        missing = [k for k in _REQUIRED_ENV if not env.get(k)]
        if missing:
            raise ReviewloopError(f"Missing environment variable: {', '.join(missing)}")
        return cls(
            env["BB_URL"],
            env["REVIEWLOOP_BITBUCKET_TOKEN"],
            env["BB_PROJECT"],
            env["BB_REPO"],
            env,
        )

    def _repo_path(self, suffix: str = "") -> str:
        base = f"rest/api/1.0/projects/{quote(self._project)}/repos/{quote(self._repo)}"
        return f"{base}/{suffix}" if suffix else base

    def _pr_path(self, number: int, suffix: str = "") -> str:
        return self._repo_path(f"pull-requests/{number}" + (f"/{suffix}" if suffix else ""))

    def _call(self, what: str, fn: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except RequestException as exc:
            raise _failure(what, exc) from exc

    def _get(self, what: str, path: str, **kwargs: Any) -> Any:
        return self._call(what, self._client.get, path, **kwargs)

    def _paged(self, what: str, path: str, params: Mapping[str, Any] | None = None) -> Iterator[dict[str, Any]]:
        start = 0
        while True:
            raw: Any = self._get(what, path, params={**(params or {}), "start": start, "limit": _PAGE})
            if not isinstance(raw, dict):
                return
            page = cast("dict[str, Any]", raw)
            yield from cast("list[dict[str, Any]]", page.get("values", []))
            nxt = page.get("nextPageStart")
            if page.get("isLastPage", True) or nxt is None:
                return
            start = int(nxt)

    def _pr_number(self) -> int:
        try:
            return int(self._env["BB_PR_ID"])
        except (KeyError, ValueError) as exc:
            raise ReviewloopError("BB_PR_ID is missing or not a number") from exc

    def bot_identity(self) -> User:
        if self._bot is None:
            response = self._get("whoami", "plugins/servlet/applinks/whoami", advanced_mode=True)
            name = response.headers.get("X-AUSERNAME")
            if not name:
                raise ReviewloopError("Bitbucket authentication returned no user")
            self._bot = _user(self._get("user lookup", f"rest/api/1.0/users/{quote(str(name))}"))
        return self._bot

    def _is_bot(self, author: Mapping[str, Any]) -> bool:
        return str(author["slug"]) == self.bot_identity().id

    def _comments(self, number: int) -> Iterator[dict[str, Any]]:
        for activity in self._paged("activities", self._pr_path(number, "activities")):
            if activity.get("action") == "COMMENTED" and isinstance(activity.get("comment"), dict):
                yield activity["comment"]

    def _summary_comment(self, number: int) -> dict[str, Any] | None:
        for comment in self._comments(number):
            text = str(comment.get("text", ""))
            has_marker = SUMMARY_MARKER in text or parse_state_marker(text) is not None
            if has_marker and not comment.get("anchor") and self._is_bot(comment["author"]):
                return comment
        return None

    def event(self) -> Event:
        kind = self._env.get("BB_EVENT", "")
        try:
            number = int(self._env["BB_PR_ID"])
        except (KeyError, ValueError):
            return Event(kind=EventKind.IGNORED)
        if kind == "pr:opened":
            if self._summary_comment(number) is not None:
                return Event(kind=EventKind.IGNORED)
            return Event(kind=EventKind.OPENED, pr_number=number)
        if kind == "pr:comment:added":
            return self._command_event(number)
        return Event(kind=EventKind.IGNORED)

    def _command_event(self, number: int) -> Event:
        lines = [ln.strip() for ln in self._env.get("BB_COMMENT", "").splitlines() if ln.strip()]
        parts = lines[0].split() if lines else []
        actor = self._env.get("BB_ACTOR", "").strip()
        if len(parts) != 2 or parts[0] != "/reviewloop" or not actor:
            return Event(kind=EventKind.IGNORED)
        try:
            command = Command(parts[1])
        except ValueError:
            return Event(kind=EventKind.IGNORED)
        return Event(
            kind=EventKind.COMMAND,
            command=command,
            actor=User(id=actor, username=actor),
            pr_number=number,
        )

    def pull_request(self) -> PullRequest:
        number = self._pr_number()
        raw: dict[str, Any] = self._get("pull request lookup", self._pr_path(number))
        try:
            source, target = raw["fromRef"], raw["toRef"]
            link_map: dict[str, Any] = raw.get("links") or {}
            links: list[dict[str, Any]] = link_map.get("self") or [{}]
            return PullRequest(
                number=int(raw["id"]),
                title=raw["title"],
                body=raw.get("description") or "",
                source_branch=source["displayId"],
                target_branch=target["displayId"],
                head_sha=source["latestCommit"],
                base_sha=target["latestCommit"],
                author=_user(raw["author"]["user"]),
                is_fork=source["repository"]["id"] != target["repository"]["id"],
                web_url=str(links[0].get("href", "")),
                draft=bool(raw.get("draft")),
            )
        except (KeyError, TypeError, IndexError) as exc:
            raise ReviewloopError("Bitbucket returned an unexpected pull request shape") from exc

    def can_write(self, user: User) -> bool:
        for suffix, allowed in (
            (self._repo_path("permissions/users"), _WRITE_PERMISSIONS),
            (f"rest/api/1.0/projects/{quote(self._project)}/permissions/users", _PROJECT_WRITE_PERMISSIONS),
        ):
            try:
                rows = list(self._paged("permission lookup", suffix, {"filter": user.username}))
            except ReviewloopError as exc:
                cause = exc.__cause__
                if _status(cause) == _NOT_FOUND:
                    return False
                raise
            if any(row["user"]["slug"] == user.id and row["permission"] in allowed for row in rows):
                return True
        return False

    def threads(self) -> list[Thread]:
        result: list[Thread] = []
        for comment in self._comments(self._pr_number()):
            if not self._is_bot(comment["author"]):
                continue
            anchor: dict[str, Any] = comment.get("anchor") or {}
            body = str(comment["text"])
            result.append(
                Thread(
                    ref=ThreadRef(id=str(comment["id"]), note_id=str(comment["id"])),
                    path=anchor.get("path"),
                    line=anchor.get("line"),
                    body=body,
                    resolved=bool(comment.get("threadResolved")) or comment.get("state") == "RESOLVED",
                    replies=[
                        Reply(
                            author=_user(r["author"]),
                            body=str(r["text"]),
                            created_at=datetime.fromtimestamp(int(r["createdDate"]) / 1000, UTC),
                        )
                        for r in _flatten_replies(comment)
                    ],
                    marker=parse_finding_marker(body),
                    side=Side.OLD if anchor.get("fileType") == "FROM" else Side.NEW,
                )
            )
        return result

    def _create(self, text: str, anchor: dict[str, Any] | None = None, parent: str | None = None) -> ThreadRef:
        data: dict[str, Any] = {"text": clean_text(text)}
        if anchor is not None:
            data["anchor"] = anchor
        if parent is not None:
            data["parent"] = {"id": int(parent)}
        created = self._call(
            "comment create", self._client.post, self._pr_path(self._pr_number(), "comments"), data=data
        )
        return ThreadRef(id=str(created["id"]), note_id=str(created["id"]))

    def post_inline(self, finding: Finding, anchor: Anchor) -> ThreadRef:
        span = f"{anchor.start_line}-{anchor.line}" if anchor.start_line is not None else str(anchor.line)
        located = f"`{anchor.path}:{span}`\n\n{finding.text}"
        if not anchor.in_diff:
            return self._create(located)
        base = {"path": anchor.path, "line": anchor.line}
        if anchor.side is Side.NEW:
            # A NEW-side line may be unchanged context rather than an added line.
            attempts = [{"lineType": "ADDED", "fileType": "TO"}, {"lineType": "CONTEXT", "fileType": "TO"}]
        else:
            attempts = [{"lineType": "REMOVED", "fileType": "FROM"}]
        for kinds in attempts:
            try:
                return self._create(finding.text, {**base, **kinds})
            except ReviewloopError as exc:
                if _status(exc.__cause__) not in _REJECTED_ANCHOR:
                    raise
        return self._create(located)

    def post_general(self, body: str) -> ThreadRef:
        return self._create(body)

    def reply(self, thread: ThreadRef, body: str) -> None:
        self._create(body, parent=thread.id)

    def _update(self, number: int, comment_id: str, **fields: Any) -> None:
        path = self._pr_path(number, f"comments/{comment_id}")
        current = self._get("comment lookup", path)
        self._call("comment update", self._client.put, path, data={"version": current["version"], **fields})

    def resolve(self, thread: ThreadRef) -> None:
        try:
            self._update(self._pr_number(), thread.id, threadResolved=True)
        except ReviewloopError as exc:
            if _status(exc.__cause__) not in _NO_RESOLVE:
                raise
            raise ReviewloopError(
                f"threadResolved requires Bitbucket Data Center 8.0 or later ({exc})"
            ) from exc.__cause__

    def upsert_summary(self, body: str) -> None:
        number = self._pr_number()
        full = body
        if SUMMARY_MARKER not in body and parse_state_marker(body) is None:
            full = f"{body}\n{SUMMARY_MARKER}"
        existing = self._summary_comment(number)
        if existing is None:
            self._create(full)
            return
        self._update(number, str(existing["id"]), text=clean_text(full))

    def summary_body(self) -> str | None:
        existing = self._summary_comment(self._pr_number())
        return None if existing is None else str(existing.get("text", ""))

    def suggestion_block(self, anchor: Anchor, code: str, replaced_lines: int = 1) -> str | None:
        return None

    def read_file(self, path: str, ref: str) -> str | None:
        try:
            raw = self._client.get(self._repo_path(f"raw/{quote(path)}"), params={"at": ref}, not_json_response=True)
        except RequestException as exc:
            if _status(exc) == _NOT_FOUND:
                return None
            raise _failure("file read", exc) from exc
        return raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)


def _flatten_replies(comment: Mapping[str, Any]) -> Iterator[dict[str, Any]]:
    children: list[dict[str, Any]] = comment.get("comments") or []
    for child in children:
        yield child
        yield from _flatten_replies(child)
