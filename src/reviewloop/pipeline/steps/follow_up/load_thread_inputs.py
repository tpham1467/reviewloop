import json
import re

from reviewloop.models import Reply, Side, Thread, User
from reviewloop.pipeline.agent import Reader, sandbox_reader, write_input
from reviewloop.pipeline.context import RunContext

# Finding ids become file names; only the shape post-comment gives them (`f-` + hex) is accepted.
_FINDING_ID = re.compile(r"f-[0-9a-f]{6,64}")
CONTEXT_LINES = 10
INPUT_DIR = "follow-up"


def _reply(r: Reply) -> dict[str, str]:
    return {"author": r.author.username, "body": r.body, "time": r.created_at.isoformat()}


def _code_at_anchor(thread: Thread, read: Reader) -> dict[str, object] | None:
    """The head file around the anchor, numbered, so the agent sees the code as it is now.

    None when there is no anchor, or when it is on the old side: that line number counts base lines, not head lines.
    """
    if thread.path is None or thread.line is None or thread.side is Side.OLD:
        return None
    text = read(thread.path)
    if text is None:
        return {"path": thread.path, "status": "missing at head"}
    lines = text.splitlines()
    line = thread.line
    start = max(line - CONTEXT_LINES, 1)
    end = min(line + CONTEXT_LINES, len(lines))
    numbered = "\n".join(f"{n}: {lines[n - 1]}" for n in range(start, end + 1))
    return {"path": thread.path, "start_line": start, "end_line": end, "text": numbered}


def _input(fid: str, thread: Thread, bot: User, read: Reader) -> str:
    assert thread.marker is not None  # noqa: S101 - only marked threads are selected
    payload = {
        "thread_id": fid,
        "marker": thread.marker.model_dump(mode="json"),
        "path": thread.path,
        "line": thread.line,
        "finding": thread.body,
        "replies": [_reply(r) for r in thread.replies if r.author.id != bot.id],
        # Earlier re-check replies of reviewloop itself, kept apart so they never count as the author's answer.
        "reviewloop_replies": [_reply(r) for r in thread.replies if r.author.id == bot.id],
        "code_at_anchor": _code_at_anchor(thread, read),
    }
    return json.dumps(payload, indent=2)


class LoadThreadInputs:
    """Load the bot's open finding threads and write one agent input file per thread (ruling R37).

    The spec's build-follow-up-context writes the transcripts; they are written here with the thread list so the
    thread data is not held twice. build-follow-up-context adds the diff.
    """

    id = "load-thread-inputs"
    group: str | None = None

    def run(self, ctx: RunContext) -> None:
        assert ctx.previous_state is not None and ctx.follow_up is not None  # noqa: S101 - set by resolve-target-turn
        follow = ctx.follow_up
        listed = set(ctx.previous_state.open)
        by_id = {t.marker.id: t for t in ctx.vcs.threads() if t.marker is not None and t.marker.id in listed}
        # A thread deleted on the platform can never be re-checked, and one resolved by hand is no longer open.
        follow.removed = [i for i in ctx.previous_state.open if i not in by_id]
        if follow.removed:
            ctx.log.info("open findings with no thread left, dropped: %s", ", ".join(follow.removed))
        follow.open_ids = [i for i in ctx.previous_state.open if i in by_id and not by_id[i].resolved]
        bot = ctx.vcs.bot_identity()
        read = sandbox_reader(ctx.sandbox)
        for fid in follow.open_ids:
            thread = by_id[fid]
            if not _FINDING_ID.fullmatch(fid):
                ctx.log.warning("finding id %r is not a reviewloop id; thread not re-checked", fid)
                continue
            follow.threads[fid] = thread
            write_input(ctx.sandbox, f"{INPUT_DIR}/{fid}.json", _input(fid, thread, bot, read))
