from collections.abc import Iterator, Mapping
from datetime import datetime
from typing import Any, Literal
from urllib.parse import quote

from atlassian.bitbucket.cloud import Cloud
from requests import RequestException

from reviewloop.errors import ReviewloopError
from reviewloop.models import (
    Anchor,
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
from reviewloop.vcs.bitbucket_http import failure as _failure
from reviewloop.vcs.bitbucket_http import http_status as _status
from reviewloop.vcs.common import COMMAND_PREFIX, SUMMARY_MARKER, parse_command

_NOT_FOUND = 404
_FORBIDDEN = 403
_WRITE_PERMISSIONS = {"admin", "write"}
_PAGE_LEN = 100

_INLINE_FALLBACK = {400, 404, 409, 422}
_UNAUTHORIZED = 401
_BOT_UUID_HINT = (
    "set REVIEWLOOP_BITBUCKET_BOT_UUID to the access token's bot account UUID "
    "(shown as the author of any comment it posts)"
)


def _norm_uuid(value: str) -> str:
    return value.strip().strip("{}").lower()


def _user(raw: Mapping[str, Any]) -> User:
    if not raw.get("uuid"):
        raise ReviewloopError("Bitbucket returned a user without a uuid")
    name = raw.get("nickname") or raw.get("display_name") or raw.get("username") or raw["uuid"]
    return User(id="{" + _norm_uuid(str(raw["uuid"])) + "}", username=str(name))


def _located(anchor: Anchor, text: str) -> str:
    span = f"{anchor.start_line}-{anchor.line}" if anchor.start_line is not None else str(anchor.line)
    return f"`{anchor.path}:{span}`\n\n{text}"


class BitbucketCloudAdapter:
    def __init__(
        self,
        workspace: str,
        repo_slug: str,
        token: str,
        env: Mapping[str, str],
        *,
        client: Cloud | None = None,
    ) -> None:
        self._env = env
        self._client: Any = client or Cloud(token=token)
        self._repo = f"repositories/{quote(workspace, safe='')}/{quote(repo_slug, safe='')}"
        self._bot: User | None = None
        self._summary: dict[str, Any] | Literal[False] | None = False
        self._pr: PullRequest | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "BitbucketCloudAdapter":
        return cls(
            env["BITBUCKET_WORKSPACE"],
            env["BITBUCKET_REPO_SLUG"],
            env["REVIEWLOOP_BITBUCKET_TOKEN"],
            env,
        )

    def _call(self, what: str, method: str, path: str, **kwargs: Any) -> Any:
        try:
            return getattr(self._client, method)(path, **kwargs)
        except RequestException as exc:
            raise _failure(what, exc) from exc

    def _paged(self, what: str, path: str) -> Iterator[dict[str, Any]]:
        page: dict[str, Any] | None = self._call(what, "get", path, params={"pagelen": _PAGE_LEN})
        while page:
            yield from page.get("values", [])
            url = page.get("next")
            page = self._call(what, "get", url, absolute=True) if url else None

    def _event_pr_id(self) -> int | None:
        raw = self._env.get("PR_ID") or self._env.get("BITBUCKET_PR_ID") or ""
        if not (raw.isascii() and raw.isdigit()) or int(raw) <= 0:
            return None
        return int(raw)

    def _pr_number(self) -> int:
        number = self._event_pr_id()
        if number is None:
            raise ReviewloopError("No pull request id in PR_ID or BITBUCKET_PR_ID")
        return number

    def _comments_path(self) -> str:
        return f"{self._repo}/pullrequests/{self._pr_number()}/comments"

    def bot_identity(self) -> User:
        if self._bot is None:
            uuid = _norm_uuid(self._env.get("REVIEWLOOP_BITBUCKET_BOT_UUID", ""))
            if uuid:
                self._bot = User(id=f"{{{uuid}}}", username=f"{{{uuid}}}")
                return self._bot
            try:
                self._bot = _user(self._call("authentication", "get", "user"))
            except ReviewloopError as exc:
                if _status(exc.__cause__) in (_UNAUTHORIZED, _FORBIDDEN):
                    raise ReviewloopError(f"Bitbucket denied GET /2.0/user: {_BOT_UUID_HINT}") from exc
                raise
        return self._bot

    def _is_bot(self, author: Mapping[str, Any]) -> bool:
        return _norm_uuid(str(author.get("uuid"))) == _norm_uuid(self.bot_identity().id)

    def _summary_comment(self, *, refresh: bool = False) -> dict[str, Any] | None:
        if self._summary is False or refresh:
            self._summary = None
            for comment in self._paged("comment list", self._comments_path()):
                body = comment["content"]["raw"]
                has_marker = SUMMARY_MARKER in body or parse_state_marker(body) is not None
                if not comment.get("deleted") and has_marker and self._is_bot(comment["user"]):
                    self._summary = comment
                    break
        return self._summary

    def event(self) -> Event:
        command = self._env.get("COMMAND", "").strip()
        number = self._event_pr_id()
        if number is None:
            return Event(kind=EventKind.IGNORED)
        if command:
            return self._command_event(command, number)
        if "BITBUCKET_PR_ID" not in self._env:
            return Event(kind=EventKind.IGNORED)
        if self._summary_comment() is not None:
            return Event(kind=EventKind.IGNORED)
        return Event(kind=EventKind.OPENED, pr_number=number)

    def _command_event(self, command: str, number: int) -> Event:
        actor = _norm_uuid(self._env.get("BITBUCKET_STEP_TRIGGERER_UUID", ""))
        # The custom pipeline's COMMAND variable holds the bare command word.
        parsed = parse_command(f"{COMMAND_PREFIX} {command}")
        if parsed is None or not actor:
            return Event(kind=EventKind.IGNORED)
        # The pipeline env exposes only the triggerer UUID, not a nickname; the UUID is what
        # can_write needs, and the username is informational, so it repeats the UUID.
        # Normalized like bot_identity, so the bot check in the CLI compares like with like.
        return Event(
            kind=EventKind.COMMAND,
            command=parsed,
            actor=User(id=f"{{{actor}}}", username=f"{{{actor}}}"),
            pr_number=number,
        )

    def pull_request(self) -> PullRequest:
        if self._pr is None:
            self._pr = self._load_pull_request()
        return self._pr

    def _load_pull_request(self) -> PullRequest:
        number = self._pr_number()
        raw = self._call("pull request read", "get", f"{self._repo}/pullrequests/{number}")
        source = raw["source"]
        dest = raw["destination"]
        head = self._call("commit read", "get", f"{self._repo}/commit/{source['commit']['hash']}")["hash"]
        base = self._call(
            "merge-base read",
            "get",
            f"{self._repo}/merge-base/{quote(dest['commit']['hash'], safe='')}..{quote(head, safe='')}",
        )["hash"]
        return PullRequest(
            number=number,
            title=raw["title"],
            body=raw.get("description") or "",
            source_branch=source["branch"]["name"],
            target_branch=dest["branch"]["name"],
            head_sha=head,
            base_sha=base,
            is_fork=source["repository"]["full_name"] != dest["repository"]["full_name"],
            draft=bool(raw.get("draft")),
        )

    def can_write(self, user: User) -> bool:
        path = f"{self._repo}/permissions-config/users/{quote(user.id, safe='')}"
        try:
            data: dict[str, Any] | None = self._client.get(path)
        except RequestException as exc:
            status = _status(exc)
            if status == _NOT_FOUND:
                return False
            if status == _FORBIDDEN:
                raise ReviewloopError(
                    "Bitbucket permission lookup denied (HTTP 403): the access token needs the repository:admin scope"
                ) from exc
            raise _failure("permission lookup", exc) from exc
        return str(data.get("permission") if data else None) in _WRITE_PERMISSIONS

    def threads(self) -> list[Thread]:
        comments = [c for c in self._paged("comment list", self._comments_path()) if not c.get("deleted")]
        by_id = {int(c["id"]): c for c in comments}

        def root_of(comment: dict[str, Any]) -> int:
            seen = {int(comment["id"])}
            while comment.get("parent") and int(comment["parent"]["id"]) in by_id:
                comment = by_id[int(comment["parent"]["id"])]
                if int(comment["id"]) in seen:
                    break
                seen.add(int(comment["id"]))
            return int(comment["id"])

        replies: dict[int, list[Reply]] = {}
        for c in comments:
            if c.get("parent"):
                replies.setdefault(root_of(c), []).append(
                    Reply(
                        author=_user(c["user"]),
                        body=c["content"]["raw"],
                        created_at=datetime.fromisoformat(c["created_on"].replace("Z", "+00:00")),
                    )
                )
        result: list[Thread] = []
        for c in comments:
            body = c["content"]["raw"]
            marker = parse_finding_marker(body)
            inline: dict[str, Any] = c.get("inline") or {}
            if c.get("parent") or not self._is_bot(c["user"]) or not (inline or marker):
                continue
            cid = int(c["id"])
            line = inline.get("to") if inline.get("to") is not None else inline.get("from")
            result.append(
                Thread(
                    ref=ThreadRef(id=str(cid), note_id=str(cid)),
                    path=inline.get("path"),
                    line=line,
                    body=body,
                    resolved=bool(c.get("resolution")),
                    replies=replies.get(cid, []),
                    marker=marker,
                    side=Side.OLD if inline.get("to") is None and inline.get("from") is not None else Side.NEW,
                )
            )
        return result

    def _create_comment(self, payload: dict[str, Any]) -> ThreadRef:
        created = self._call("comment create", "post", self._comments_path(), json=payload)
        cid = str(created["id"])
        return ThreadRef(id=cid, note_id=cid)

    def post_inline(self, finding: Finding, anchor: Anchor) -> ThreadRef:
        located = {"content": {"raw": _located(anchor, finding.text)}}
        if not anchor.in_diff:
            return self._create_comment(located)
        key = "to" if anchor.side is Side.NEW else "from"
        payload = {"content": {"raw": finding.text}, "inline": {"path": anchor.path, key: anchor.line}}
        try:
            return self._create_comment(payload)
        except ReviewloopError as exc:
            if _status(exc.__cause__) not in _INLINE_FALLBACK:
                raise
            return self._create_comment(located)

    def post_general(self, body: str) -> ThreadRef:
        return self._create_comment({"content": {"raw": body}})

    def reply(self, thread: ThreadRef, body: str) -> None:
        self._create_comment({"content": {"raw": body}, "parent": {"id": int(thread.id)}})

    def resolve(self, thread: ThreadRef) -> None:
        self._call("comment resolve", "post", f"{self._comments_path()}/{int(thread.id)}/resolve")

    def upsert_summary(self, body: str) -> None:
        if SUMMARY_MARKER not in body and parse_state_marker(body) is None:
            body = f"{body}\n{SUMMARY_MARKER}"
        existing = self._summary_comment(refresh=True)
        if existing is None:
            ref = self.post_general(body)
            self._summary = {"id": int(ref.id)}
            return
        self._call(
            "comment update",
            "put",
            f"{self._comments_path()}/{int(existing['id'])}",
            data={"content": {"raw": body}},
        )

    def summary_body(self) -> str | None:
        existing = self._summary_comment(refresh=True)
        return None if existing is None else str(existing["content"]["raw"])

    def suggestion_block(self, anchor: Anchor, code: str, replaced_lines: int = 1) -> str | None:
        return None

    def read_file(self, path: str, ref: str) -> str | None:
        url = f"{self._repo}/src/{quote(ref, safe='')}/{quote(path, safe='/')}"
        try:
            content = self._client.get(url, not_json_response=True)
        except RequestException as exc:
            if _status(exc) == _NOT_FOUND:
                return None
            raise _failure("file read", exc) from exc
        return bytes(content).decode("utf-8", errors="replace")
