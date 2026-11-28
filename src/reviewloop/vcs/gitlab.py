import json
import os
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, cast

import gitlab
from gitlab.exceptions import GitlabCreateError, GitlabGetError

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
from reviewloop.state import parse_finding_marker
from reviewloop.vcs.common import SUMMARY_MARKER, located, parse_command, suggestion_fence, with_suggestion

_DEVELOPER = 30
_MAX_SUGGESTION_LINES = 30
_NOT_FOUND = 404


def _user(raw: Mapping[str, Any]) -> User:
    return User(id=str(raw["id"]), username=str(raw["username"]))


class GitLabAdapter:
    def __init__(
        self,
        url: str,
        token: str,
        project_id: str,
        env: Mapping[str, str],
        *,
        client: gitlab.Gitlab | None = None,
    ) -> None:
        self._env = env
        self._gl = client or gitlab.Gitlab(url, private_token=token)
        self._project_id = project_id
        self._project: Any = None
        self._bot: User | None = None
        self._mrs: dict[int, Any] = {}
        self._payload: dict[str, Any] | Literal[False] | None = False

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "GitLabAdapter":
        return cls(
            env["CI_SERVER_URL"],
            env["REVIEWLOOP_GITLAB_TOKEN"],
            env["CI_PROJECT_ID"],
            env,
        )

    @property
    def project(self) -> Any:
        if self._project is None:
            self._project = self._gl.projects.get(self._project_id)
        return self._project

    def _mr(self, iid: int | None = None) -> Any:
        number = iid if iid is not None else int(self._env["CI_MERGE_REQUEST_IID"])
        if number not in self._mrs:
            self._mrs[number] = self.project.mergerequests.get(number)
        return self._mrs[number]

    def bot_identity(self) -> User:
        if self._bot is None:
            self._gl.auth()
            user = self._gl.user
            if user is None:
                raise ReviewloopError("GitLab authentication returned no user")
            self._bot = User(id=str(user.id), username=str(user.username))
        return self._bot

    def _is_bot(self, author: Mapping[str, Any]) -> bool:
        return str(author["id"]) == self.bot_identity().id

    def _summary_note(self, mr: Any) -> Any:
        for note in mr.notes.list(iterator=True):
            if SUMMARY_MARKER in note.body and self._is_bot(note.author):
                return note
        return None

    def event(self) -> Event:
        source = self._env.get("CI_PIPELINE_SOURCE", "")
        if source == "merge_request_event":
            mr = self._mr()
            if self._summary_note(mr) is not None:
                return Event(kind=EventKind.IGNORED)
            return Event(kind=EventKind.OPENED, pr_number=int(mr.iid))
        if source == "trigger":
            return self._trigger_event()
        return Event(kind=EventKind.IGNORED)

    def _load_payload(self) -> dict[str, Any] | None:
        if self._payload is False:
            self._payload = self._read_payload()
        return self._payload

    def _read_payload(self) -> dict[str, Any] | None:
        raw = self._env.get("TRIGGER_PAYLOAD", "")
        try:
            text = Path(raw).read_text(encoding="utf-8") if os.path.isfile(raw) else raw
            data: Any = json.loads(text)
        except (OSError, ValueError):
            return None
        return cast("dict[str, Any]", data) if isinstance(data, dict) else None

    def _trigger_event(self) -> Event:
        payload = self._load_payload()
        if payload is None:
            return Event(kind=EventKind.IGNORED)
        try:
            attrs: dict[str, Any] = payload.get("object_attributes") or {}
            mr: dict[str, Any] = payload.get("merge_request") or {}
            if attrs.get("noteable_type") != "MergeRequest":
                return Event(kind=EventKind.IGNORED)
            command = parse_command(str(attrs.get("note", "")))
            if command is None:
                return Event(kind=EventKind.IGNORED)
            return Event(
                kind=EventKind.COMMAND,
                command=command,
                actor=_user(payload["user"]),
                pr_number=int(mr["iid"]),
            )
        except (ValueError, KeyError, TypeError, AttributeError):
            return Event(kind=EventKind.IGNORED)

    def pull_request(self) -> PullRequest:
        mr = self._mr(self._pr_number())
        return PullRequest(
            number=int(mr.iid),
            title=mr.title,
            body=mr.description or "",
            source_branch=mr.source_branch,
            target_branch=mr.target_branch,
            head_sha=mr.diff_refs["head_sha"],
            base_sha=mr.diff_refs["base_sha"],
            is_fork=mr.source_project_id != mr.target_project_id,
            # Older GitLab versions only send work_in_progress.
            draft=bool(getattr(mr, "draft", False) or getattr(mr, "work_in_progress", False)),
        )

    def _pr_number(self) -> int | None:
        if "CI_MERGE_REQUEST_IID" in self._env:
            return None
        event = self._trigger_event()
        return event.pr_number

    def can_write(self, user: User) -> bool:
        try:
            member = self.project.members_all.get(int(user.id))
        except GitlabGetError as exc:
            if exc.response_code == _NOT_FOUND:
                return False
            raise ReviewloopError(f"GitLab member lookup failed (HTTP {exc.response_code})") from exc
        return int(member.access_level) >= _DEVELOPER

    def threads(self) -> list[Thread]:
        mr = self._mr(self._pr_number())
        result: list[Thread] = []
        for disc in mr.discussions.list(iterator=True):
            notes: list[dict[str, Any]] = disc.attributes["notes"]
            first = notes[0]
            if not self._is_bot(first["author"]):
                continue
            pos: dict[str, Any] = first.get("position") or {}
            side_new = pos.get("new_line") is not None
            result.append(
                Thread(
                    ref=ThreadRef(id=str(disc.id), note_id=str(first["id"])),
                    path=(pos.get("new_path") if side_new else pos.get("old_path")),
                    line=(pos.get("new_line") if side_new else pos.get("old_line")),
                    body=first["body"],
                    resolved=bool(first.get("resolved", False)),
                    replies=[
                        Reply(
                            author=_user(n["author"]),
                            body=n["body"],
                            created_at=datetime.fromisoformat(n["created_at"].replace("Z", "+00:00")),
                        )
                        for n in notes[1:]
                        if not n.get("system")
                    ],
                    marker=parse_finding_marker(first["body"]),
                    side=Side.OLD if pos and not side_new else Side.NEW,
                )
            )
        return result

    def _finding_body(self, finding: Finding, anchor: Anchor) -> str:
        lines = anchor.line - (anchor.start_line or anchor.line) + 1
        if not finding.suggestion or finding.security or lines > _MAX_SUGGESTION_LINES:
            return finding.text
        block = self.suggestion_block(anchor, finding.suggestion, replaced_lines=lines)
        return with_suggestion(finding.text, block) if block else finding.text

    def post_inline(self, finding: Finding, anchor: Anchor) -> ThreadRef:
        mr = self._mr(self._pr_number())
        general = {"body": located(anchor, finding.text)}
        if not anchor.in_diff:
            return self._create_discussion(mr, general)
        refs = mr.diff_refs
        position: dict[str, Any] = {
            "position_type": "text",
            "base_sha": refs["base_sha"],
            "start_sha": refs["start_sha"],
            "head_sha": refs["head_sha"],
        }
        if anchor.side is Side.NEW:
            position.update(new_path=anchor.path, new_line=anchor.line)
        else:
            position.update(old_path=anchor.path, new_path=anchor.path, old_line=anchor.line)
        try:
            return self._create_discussion(mr, {"body": self._finding_body(finding, anchor), "position": position})
        except GitlabCreateError:
            return self._create_discussion(mr, general)

    @staticmethod
    def _create_discussion(mr: Any, data: dict[str, Any]) -> ThreadRef:
        disc = mr.discussions.create(data)
        notes: list[dict[str, Any]] = disc.attributes.get("notes") or []
        note_id = str(notes[0]["id"]) if notes else None
        return ThreadRef(id=str(disc.id), note_id=note_id)

    def post_general(self, body: str) -> ThreadRef:
        return self._create_discussion(self._mr(self._pr_number()), {"body": body})

    def reply(self, thread: ThreadRef, body: str) -> None:
        disc = self._mr(self._pr_number()).discussions.get(thread.id)
        disc.notes.create({"body": body})

    def resolve(self, thread: ThreadRef) -> None:
        disc = self._mr(self._pr_number()).discussions.get(thread.id)
        disc.resolved = True
        disc.save()

    def upsert_summary(self, body: str) -> None:
        mr = self._mr(self._pr_number())
        note = self._summary_note(mr)
        full = body if SUMMARY_MARKER in body else f"{body}\n\n{SUMMARY_MARKER}"
        if note is None:
            mr.notes.create({"body": full})
            return
        note.body = full
        note.save()

    def summary_body(self) -> str | None:
        note = self._summary_note(self._mr(self._pr_number()))
        return None if note is None else str(note.body)

    def suggestion_block(self, anchor: Anchor, code: str, replaced_lines: int = 1) -> str | None:
        if anchor.side is not Side.NEW or not anchor.in_diff:
            return None
        fence = suggestion_fence(code)
        return f"{fence}suggestion:-{max(replaced_lines, 1) - 1}+0\n{code}\n{fence}"

    def read_file(self, path: str, ref: str) -> str | None:
        try:
            return self.project.files.get(file_path=path, ref=ref).decode().decode("utf-8")
        except GitlabGetError as exc:
            if exc.response_code == _NOT_FOUND:
                return None
            raise ReviewloopError(f"GitLab file read failed (HTTP {exc.response_code})") from exc
